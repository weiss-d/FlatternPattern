#!/usr/bin/env python
"""FlatternPattern: replace pattern fills with real, tiled geometry."""

from typing import NamedTuple

import inkex
from inkex.styles import Style

from fp_clipped import flatten_fill
from fp_resolve import ResolvedPattern, SkipError, resolve
from fp_svg import set_props
from fp_tiling import TileLimitError, TilePlan, inkscape_view_differs, plan

# Containers whose content does not render directly, so the walk skips them.
NON_RENDERED = {
    "defs",
    "clipPath",
    "mask",
    "marker",
    "pattern",
    "symbol",
    "metadata",
    "title",
    "desc",
    "style",
    "script",
}
TEXT_ROOTS = (inkex.TextElement, inkex.FlowRoot)


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
        case inkex.Pattern() as server, (
            inkex.PathElement()
            | inkex.Rectangle()
            | inkex.Circle()
            | inkex.Ellipse()
            | inkex.Line()
            | inkex.Polyline()
        ):
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
    pattern: ResolvedPattern
    plan: TilePlan
    bbox: inkex.BoundingBox  # the geometric box of the target before the flattening


def prepare(targets, viewport, limit: int, inkscape_obb: bool = False):
    """Resolve and plan each target, with no change to the document.

    Return (jobs, skips, empty). empty holds the targets whose pattern paints
    nothing. If all targets together need more than limit tiles, raise
    TileLimitError with a lower bound of the total. inkscape_obb goes to plan()."""
    # One resolve for each pattern element. The value is a ResolvedPattern or the
    # SkipError of the first try, so a broken pattern is not resolved again.
    resolved: dict = {}
    jobs, skips, empty = [], [], []
    used = 0
    for target in targets:
        if target.prop == "stroke":
            skips.append(Skip(target.elem, "The extension does not flatten stroke patterns yet."))
            continue
        try:
            if target.pattern not in resolved:
                try:
                    resolved[target.pattern] = resolve(target.pattern)
                except SkipError as err:
                    resolved[target.pattern] = err
            rp = resolved[target.pattern]
            if isinstance(rp, SkipError):
                raise rp
            bbox = target.elem.path.bounding_box()
            tile_plan = (
                plan(rp, bbox, bbox, viewport, limit - used, inkscape_obb)
                if rp.content and bbox
                else None
            )
        except SkipError as err:
            skips.append(Skip(target.elem, str(err)))
            continue
        except TileLimitError as err:
            raise TileLimitError(used + err.args[0]) from err
        if tile_plan is None:
            empty.append(target)
        else:
            jobs.append(Job(target, rp, tile_plan, bbox))
            used += len(tile_plan.cells)
    return jobs, skips, empty


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
                "Select one or more objects with a pattern fill, then run the extension again."
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
        skips += more_skips
        moved = (
            []
            if inkscape_obb
            else [
                job.target.elem
                for job in jobs
                if inkscape_view_differs(job.pattern, job.plan.tile, job.bbox)
            ]
        )

        # Only clipped mode exists. Merged mode also makes clipped copies.
        for job in jobs:
            flatten_fill(job.target.elem, job.pattern, job.plan)
        for target in empty:
            set_props(target.elem, {"fill": "none"})

        lines = []
        if self.options.mode == "merged" and jobs:
            lines.append("Merged mode does not exist yet. The extension made clipped copies.")
        lines += [
            f"Removed the fill of {label(t.elem)}: the pattern paints nothing." for t in empty
        ]
        lines += [
            f"Note for {label(e)}: Inkscape 1.4 shows this pattern at another place. "
            "The output follows the SVG standard. To keep the Inkscape view, select "
            '"Match the Inkscape view, not the SVG standard", then run the extension again.'
            for e in moved
        ]
        lines += [f"Skipped {label(s.elem)}: {s.reason}" for s in skips]
        if not jobs and not empty and not skips:
            lines.append("The selection has no pattern fill to flatten.")
        if lines:
            self.msg("\n".join(lines))


if __name__ == "__main__":
    FlatternPattern().run()
