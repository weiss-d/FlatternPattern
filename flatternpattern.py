#!/usr/bin/env python
"""FlatternPattern: replace pattern fills and strokes with real, tiled geometry."""

from copy import deepcopy
from typing import NamedTuple

import inkex
from inkex.styles import Style

from fp_cleanup import remove_unused
from fp_clipped import SHAPES, Part, flatten_element
from fp_merged import MergeError, merge_fills, merged_layer
from fp_resolve import ResolvedPattern, SkipError, chain, resolve
from fp_svg import NON_RENDERED, set_props, unique_id
from fp_tiling import TileLimitError, inkscape_view_differs, plan, shape_path, stroke_region

TEXT_ROOTS = (inkex.TextElement, inkex.FlowRoot)
STROKE_NOTE = "merged mode does not merge pattern strokes."


class Target(NamedTuple):
    elem: inkex.BaseElement
    prop: str  # "fill" or "stroke"
    pattern: inkex.Pattern


class Skip(NamedTuple):
    elem: inkex.BaseElement
    reason: str


def walk(elem, parent_style: Style):
    """Yield (element, specified style) for elem and its rendered descendants.

    Each style comes from the parent style, so every element gets one cascade."""
    if not isinstance(elem.tag, str) or elem.TAG in NON_RENDERED:
        return  # comments, processing instructions, non-rendered containers
    style = Style.add_inherited(Style.cascaded_style(elem), parent_style)
    style.element = elem
    yield elem, style
    for child in elem:
        yield from walk(child, style)


def paint(style: Style, prop: str):
    """Return the parsed paint of prop: a paint server element, a color, None,
    or the ValueError that inkex raises for a url() to a missing element.

    The url() lookup uses style.element, which walk() sets."""
    try:
        return style(prop)
    except ValueError as err:
        return err


def text_root(elem):
    """Return the text or flowRoot element that holds elem (or elem itself), else None.

    Text children such as tspan, and the shapes in a flowRegion, belong to their text."""
    return next((a for a in (elem, *elem.ancestors()) if isinstance(a, TEXT_ROOTS)), None)


def classify(elem, prop: str, style: Style) -> Target | Skip | None:
    """Make a Target for a pattern paint, a Skip for an unsupported one,
    or None when there is nothing to flatten."""
    match paint(style, prop), elem:
        case ValueError(), _:
            return Skip(elem, f"The {prop} refers to an element that is not in the document.")
        case inkex.BaseElement(TAG="hatch"), _:
            return Skip(elem, f"The {prop} is a hatch. The extension does not support hatches.")
        case inkex.MeshGradient(), _:
            return Skip(
                elem,
                f"The {prop} is a mesh gradient. The extension does not support mesh gradients.",
            )
        case inkex.Pattern(), _ if (text := text_root(elem)) is not None:
            return Skip(
                text,
                "This is a text object. Use Path > Object to Path, then run the extension again.",
            )
        case inkex.Pattern(), inkex.Use():
            return Skip(
                elem,
                "This is a clone. Use Edit > Clone > Unlink Clone, then run the extension again.",
            )
        case inkex.Pattern() as server, _ if isinstance(elem, SHAPES):
            return Target(elem, prop, server)
        case inkex.Pattern() | inkex.Gradient(), _:
            # Gradients need no flattening. A group gives the pattern to its
            # children, and an image or other element does not use the paint.
            return None
        case inkex.BaseElement() as server, _ if server.TAG.lower() != "solidcolor":
            return Skip(
                elem, f"The {prop} refers to a {server.TAG} element, which is not a paint server."
            )
        case _:
            # Colors, none, context-fill and solid colors need no flattening.
            return None


def collect(selection, stroke_patterns: bool):
    """Find the pattern-painted shapes in the selection and its groups."""
    chosen = set(selection)
    # A selected element inside a selected group comes from the walk of the group.
    tops = [e for e in selection if not any(a in chosen for a in e.ancestors())]
    props = ("fill", "stroke") if stroke_patterns else ("fill",)
    results = [
        classify(elem, prop, style)
        for top in tops
        for elem, style in walk(top, parent_style(top))
        for prop in props
    ]
    # The children of one text give the same Skip, so keep each one only once.
    return (
        [r for r in results if isinstance(r, Target)],
        list(dict.fromkeys(r for r in results if isinstance(r, Skip))),
    )


def parent_style(elem) -> Style:
    """Return the specified style of the parent, or an empty style for the root."""
    parent = elem.getparent()
    return Style() if parent is None else parent.specified_style()


def label(elem) -> str:
    """Name elem in a message by its id, or by its tag when it has no id."""
    # get_id() writes a new id onto an element without one, so use get("id").
    eid = elem.get("id")
    return f"#{eid}" if eid else f"a <{elem.TAG}> element"


class Job(NamedTuple):
    target: Target
    part: Part
    bbox: inkex.BoundingBox  # the geometric box of the target before the flattening


def region_of(target: Target, path, bbox, viewport) -> inkex.BoundingBox | None:
    """Return the area that the paint of target covers, or None when it paints nothing."""
    if target.prop == "fill":
        return bbox
    style = target.elem.specified_style()
    if str(style.get("vector-effect", "none")) == "non-scaling-stroke":
        raise SkipError(
            "The stroke does not scale with the object. "
            "The extension does not support vector-effect:non-scaling-stroke."
        )
    return stroke_region(path, style, viewport)


def prepare(targets, viewport, limit: int, inkscape_obb: bool = False):
    """Resolve and plan each target, with no change to the document.

    Return (jobs, skips, empty). empty holds the targets whose pattern paints
    nothing. If all targets together need more than limit tiles, raise
    TileLimitError with a lower bound of the total. inkscape_obb goes to plan()."""
    cache: dict = {}
    jobs, skips, empty = [], [], []
    used = 0
    for target in targets:
        try:
            rp = resolve_once(cache, target.pattern)
            job = plan_target(target, rp, viewport, limit - used, inkscape_obb)
        except SkipError as err:
            skips.append(Skip(target.elem, str(err)))
            continue
        except TileLimitError as err:
            raise TileLimitError(used + err.args[0]) from err
        if job is None:
            empty.append(target)
        else:
            jobs.append(job)
            used += len(job.part.plan.cells)
    return jobs, skips, empty


def resolve_once(cache: dict, pattern) -> ResolvedPattern:
    """Resolve pattern, or raise its SkipError.

    cache holds the ResolvedPattern or the SkipError of each pattern element, so
    the function resolves each pattern only one time."""
    if pattern not in cache:
        try:
            cache[pattern] = resolve(pattern)
        except SkipError as err:
            cache[pattern] = err
    if isinstance(cache[pattern], SkipError):
        raise cache[pattern]
    return cache[pattern]


def plan_target(target: Target, rp: ResolvedPattern, viewport, limit: int, inkscape_obb: bool):
    """Return the Job for target, or None when its pattern paints nothing."""
    path = shape_path(target.elem, viewport)
    bbox = path.bounding_box()
    if not rp.content or not bbox:
        return None
    region = region_of(target, path, bbox, viewport)
    if region is None:
        return None
    tile_plan = plan(rp, bbox, region, viewport, limit, inkscape_obb)
    return None if tile_plan is None else Job(target, Part(rp, tile_plan, region), bbox)


def moved_elements(jobs) -> list:
    """Return each element whose pattern Inkscape 1.4 shows at another place
    than the SVG standard."""
    return list(
        dict.fromkeys(
            job.target.elem
            for job in jobs
            if inkscape_view_differs(job.part.pattern, job.part.plan.tile, job.bbox)
        )
    )


def keep_originals(elems):
    """Put a hidden copy of each element in elems before it, with the id ID-original.

    The copy keeps the patterns, the transform and all other properties of the
    element. The descendants of the copy, for example a title, get no id."""
    for elem in elems:
        copy = deepcopy(elem)
        for node in copy.iterdescendants():
            if isinstance(node.tag, str):
                node.attrib.pop("id", None)
        copy.set("id", unique_id(elem.root, f"{elem.get_id()}-original"))
        set_props(copy, {"display": "none"})
        elem.addprevious(copy)


def apply(jobs, empty, tolerance: float | None = None, keep_original: bool = False):
    """Change the document: flatten the jobs and remove the paints in empty.

    An element with a pattern fill and a pattern stroke gets one output group.
    tolerance is the seam overlap of merged mode, or None for clipped mode.
    With keep_original, each changed element gets a hidden copy (see keep_originals()).
    Return (notes, empty): the notes about merged mode for the user, and empty
    with the merged fills that paint nothing."""
    parts: dict = {}
    for job in jobs:
        parts.setdefault(job.target.elem, {})[job.target.prop] = job.part
    if keep_original:
        keep_originals(dict.fromkeys([*parts, *(t.elem for t in empty)]))
    no_merge = ({}, [], [])
    layers, removed, notes = no_merge if tolerance is None else merged_layers(parts, tolerance)
    for elem in removed:
        del parts[elem]["fill"]
    empty = empty + [Target(elem, "fill", None) for elem in removed]
    for elem, paints in parts.items():
        if paints:
            fill, stroke = paints.get("fill"), paints.get("stroke")
            flatten_element(elem, fill, stroke, layers.get(elem))
    for target in empty:
        set_props(target.elem, {target.prop: "none"})
    return notes, empty


def merged_layers(parts: dict, tolerance: float):
    """Return (layers, removed, notes) for merged mode. layers maps each element
    to its merged fill layer. removed holds the elements whose fill paints
    nothing. An element without a layer gets clipped copies."""
    layers, removed, notes = {}, [], []
    fills = {elem: paints["fill"] for elem, paints in parts.items() if "fill" in paints}
    # All elements are in one document, so one search for a stylesheet is enough.
    reset = bool(fills) and bool(next(iter(fills)).root.xpath("//svg:style"))
    failed: dict = {}  # the elements for each error text, for one note per error
    for elem, merged in merge_fills(fills, tolerance, reset).items():
        if isinstance(merged, MergeError):
            failed.setdefault(str(merged), []).append(label(elem))
        elif merged is None:
            removed.append(elem)
        else:
            layers[elem] = merged_layer(elem, merged, reset)
            notes += merge_notes(elem, merged.content)
    notes += [
        f"Made clipped copies for the fill of {', '.join(labels)}: {error}"
        for error, labels in failed.items()
    ]
    notes += [
        f"Made clipped copies for the stroke of {label(elem)}: {STROKE_NOTE}"
        for elem, paints in parts.items()
        if "stroke" in paints
    ]
    return layers, removed, notes


def merge_notes(elem, content) -> list[str]:
    """Return the notes about what a merged path of content cannot show."""
    notes = []
    if len(set(content.colors)) > 1:
        notes.append(
            f"The tile content of {label(elem)} has more than one color. "
            f"The merged path uses the first color, {content.colors[0]}."
        )
    if content.translucent:
        notes.append(
            f"The tile content of {label(elem)} has an opacity below 1. The merged path is opaque."
        )
    return notes


def processed_patterns(jobs, empty) -> list:
    """Return the pattern elements in the href chains of the processed targets."""
    starts = [j.target.pattern for j in jobs] + [t.pattern for t in empty if t.pattern is not None]
    return list(dict.fromkeys(p for start in starts for p in chain(start)))


def report(jobs, empty, skips, moved, notes) -> list[str]:
    """Return the message lines for the user."""
    lines = list(notes)
    lines += [
        f"Removed the {t.prop} of {label(t.elem)}: the pattern paints nothing." for t in empty
    ]
    lines += [
        f"Note for {label(e)}: Inkscape 1.4 shows this pattern at another place. "
        "The output follows the SVG standard. To keep the Inkscape view, select "
        '"Match the Inkscape view, not the SVG standard", then run the extension again.'
        for e in moved
    ]
    lines += [f"Skipped {label(s.elem)}: {s.reason}" for s in skips]
    if not jobs and not empty and not skips:
        lines.append("The selection has no pattern fill or pattern stroke to flatten.")
    return lines


class FlatternPattern(inkex.EffectExtension):
    def add_arguments(self, pars):
        pars.add_argument("--tab", default="options")
        pars.add_argument("--mode", default="clipped", choices=("clipped", "merged"))
        pars.add_argument("--stroke_patterns", type=inkex.Boolean, default=True)
        pars.add_argument("--keep_original", type=inkex.Boolean, default=False)
        pars.add_argument("--cleanup_defs", type=inkex.Boolean, default=True)
        pars.add_argument("--tile_limit", type=int, default=2000)
        pars.add_argument("--tolerance", type=float, default=0.01)
        pars.add_argument("--inkscape_obb", type=inkex.Boolean, default=False)

    def effect(self):
        if not self.svg.selection:
            raise inkex.AbortExtension(
                "Select one or more objects with a pattern fill or a pattern stroke, "
                "then run the extension again."
            )
        targets, skips = collect(list(self.svg.selection.values()), self.options.stroke_patterns)
        viewport = (self.svg.viewbox_width, self.svg.viewbox_height)
        limit = self.options.tile_limit
        inkscape_obb = self.options.inkscape_obb
        try:
            jobs, more_skips, empty = prepare(targets, viewport, limit, inkscape_obb)
        except TileLimitError as err:
            raise inkex.AbortExtension(
                f"The selection needs at least {err.args[0]} tiles. The limit is {limit}. "
                "Increase the maximum number of tiles, then run the extension again."
            ) from err
        moved = [] if inkscape_obb else moved_elements(jobs)
        tolerance = self.options.tolerance if self.options.mode == "merged" else None
        notes, empty = apply(jobs, empty, tolerance, self.options.keep_original)
        if self.options.cleanup_defs:
            remove_unused(self.svg, processed_patterns(jobs, empty))
        lines = report(jobs, empty, skips + more_skips, moved, notes)
        if lines:
            self.msg("\n".join(lines))


if __name__ == "__main__":
    FlatternPattern().run()
