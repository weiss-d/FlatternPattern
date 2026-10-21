"""Merged-outlines output: one filled path for the pattern fill of a target.

The extension runs Inkscape two times on temporary documents:

1. Tiles: for each pattern tile, Inkscape converts the strokes of the content to
   paths, joins all shapes into one path and cuts that path to the tile box. The
   tile box grows by the seam overlap on each side, so the copies of adjacent
   tiles overlap a little and the join in step 2 leaves no gaps.
2. Targets: for each target, one path holds a copy of the tile path for each
   tile. Inkscape joins the copies and cuts the result to the shape of the target.

Each step is one Inkscape run for all tiles or all targets, because each start
of Inkscape takes about 1.5 s. One path with all copies makes step 2 about 12
times faster than one path for each tile (see docs/spike-merged-mode.md).
"""

import math
import os
from tempfile import TemporaryDirectory
from typing import NamedTuple

import inkex
from inkex.command import CommandNotFound, ProgramRunError, inkscape_command
from lxml import etree

from fp_clipped import (
    GROUP_PROPS,
    MARKERS,
    SHAPES,
    UNBOUNDED,
    Part,
    geometry_copy,
    paint_style,
    styled_copy,
    transform_within,
)
from fp_svg import (
    RESET_STYLE,
    box_path,
    fmt_path,
    fmt_transform,
    opacity_value,
    rendered,
    set_props,
    set_raw,
    unique_id,
)
from fp_tiling import encloses_area, overlap_of, tile_transform

# Content with one of these properties cannot become one filled path. A
# vector-effect makes the stroke width depend on the screen, not on the tile.
NOT_MERGEABLE = ("filter", "mask", "clip-path", "vector-effect", *MARKERS)


class MergeError(Exception):
    """Merged mode cannot make this target. The argument tells the user why."""


class Content(NamedTuple):
    shapes: list  # copies of the painted shapes, with their transform to pattern space
    colors: list[str]  # the painted colors, in paint order
    opacity: float  # the lowest opacity of a painted shape, 1 when all are opaque


class Merged(NamedTuple):
    d: str  # the path data of the merged fill, in the user space of the target
    content: Content  # the tile content that the path comes from


class Item(NamedTuple):
    """One group of a temporary document and the actions for it."""

    group: inkex.Group
    actions: list[str]
    container: str  # the id of the element that holds the result
    cut: str  # the id of the shape that the last intersection removes


def tile_actions(k: int) -> list[str]:
    """Return the actions that make the tile path of tile k (see tile_paths())."""
    return [
        f"select-by-selector:#content{k} > *",
        "object-stroke-to-path",
        "select-clear",
        # A shape with a fill and a stroke becomes a group of two paths.
        f"select-by-selector:#content{k} > g",
        "selection-ungroup",
        "select-clear",
        f"select-by-selector:#content{k} > *",
        "path-union",
        "select-clear",
        f"select-by-selector:#content{k} > *, #tile{k}",
        "path-intersection",
        "select-clear",
    ]


def target_actions(k: int) -> list[str]:
    """Return the actions that make the merged fill of target k (see merge_targets())."""
    return [
        f"select-by-id:tiles{k}",
        "path-union",
        "select-clear",
        f"select-by-selector:#target{k} > *",
        "path-intersection",
        "select-clear",
    ]


def run(groups: list, actions: list[str], frame: dict):
    """Run Inkscape with actions on a document that holds groups. Return the
    root of the result.

    frame holds the width, height and viewBox of the source document. Then a
    percentage in a copied shape refers to the same viewport as in the source.
    The actions go in a file, because a long command line fails on Windows."""
    svg = inkex.load_svg(b'<svg xmlns="http://www.w3.org/2000/svg"/>').getroot()
    for name, value in frame.items():
        svg.set(name, value)
    for group in groups:
        svg.append(group)
    with TemporaryDirectory(prefix="flatternpattern") as tmpdir:
        actions_file = os.path.join(tmpdir, "actions.txt")
        with open(actions_file, "w", encoding="utf-8") as handle:
            handle.write(";".join(actions))
        try:
            out = inkscape_command(etree.tostring(svg), actions_file=actions_file)
        except (CommandNotFound, ProgramRunError, OSError) as err:
            raise MergeError(f"Inkscape did not run: {err}") from err
    try:
        return inkex.load_svg(out).getroot()
    except (etree.XMLSyntaxError, ValueError) as err:
        raise MergeError(f"Inkscape gave a document that is not valid: {err}") from err


def frame_of(svg) -> dict:
    """Return the width, height and viewBox of the document svg (see run())."""
    return {name: svg.get(name) for name in ("width", "height", "viewBox") if svg.get(name)}


def group(gid: str, *children):
    """Return a new group with the id gid and children.

    inkex.Group.new() does not append its children, so this function does."""
    result = inkex.Group()
    result.set("id", gid)
    for child in children:
        result.append(child)
    return result


def run_all(items: list, frame: dict) -> list:
    """Run Inkscape one time on items. Return the result of each item: the path
    from result_path(), None, or the MergeError of that item. frame goes to run()."""
    try:
        root = run([i.group for i in items], [a for i in items for a in i.actions], frame)
    except MergeError as err:
        return [err] * len(items)
    results = []
    for item in items:
        try:
            results.append(result_path(root, item.container, item.cut))
        except MergeError as err:
            results.append(err)
    return results


def black(elem, eid: str, fill_rule: str = "nonzero"):
    """Give elem the id eid and a black fill with fill_rule. Return elem."""
    elem.set("id", eid)
    elem.set("style", f"fill:#000;fill-rule:{fill_rule}")
    return elem


def result_path(root, container: str, cut: str) -> inkex.Path | None:
    """Return the path data of the result in the element container of root, with
    its transform, or None when the result is empty.

    cut is the id of the shape that the last intersection used. The intersection
    removes it, so a cut that is still there shows that an action did not run."""
    parent = root.getElementById(container)
    if parent is None:
        raise MergeError("Inkscape did not give a result.")
    children = [c for c in parent if isinstance(c.tag, str)]
    if root.getElementById(cut) is not None or len(children) > 1:
        raise MergeError("Inkscape did not complete the boolean operations.")
    if not children:
        return None
    if not isinstance(children[0], inkex.PathElement):
        raise MergeError("Inkscape did not give a path.")
    return inkex.Path(children[0].attrib.get("d", "")).transform(
        inkex.Transform(children[0].attrib.get("transform"))
    )


def up_to(elem, top) -> list:
    """Return elem and its ancestors up to top, with top."""
    nodes = [elem]
    while nodes[-1] is not top and nodes[-1].getparent() is not None:
        nodes.append(nodes[-1].getparent())
    return nodes


def hidden(elem, top) -> bool:
    """Return True when visibility hides elem, or when elem or a group between
    elem and top has display:none."""
    if str(elem.style.get("visibility", "visible")) != "visible":
        return True
    return any(str(n.style.get("display", "inline")) == "none" for n in up_to(elem, top))


def shown_opacity(elem, top, props) -> float:
    """Return the lowest opacity with which a paint in props of elem shows: the
    product of the opacity of elem and the groups up to top, and the
    lowest fill-opacity or stroke-opacity of elem.

    The copies have their full specified style, so elem holds the inherited
    fill-opacity and stroke-opacity."""
    groups = math.prod(opacity_value(n.style.get("opacity", "1")) for n in up_to(elem, top))
    return groups * min(opacity_value(elem.style.get(f"{p}-opacity", "1")) for p in props)


def paints(elem, top) -> dict:
    """Return the color of each paint that elem shows: the fill, then the stroke.

    A paint with an opacity of 0 shows nothing, for example an invisible
    background rect, so it is not in the result."""
    found = {}
    for prop, initial in (("fill", "black"), ("stroke", "none")):
        value = str(elem.style.get(prop, initial)).strip()
        if value == "none" or (prop == "fill" and not encloses_area(elem.path)):
            continue
        if shown_opacity(elem, top, [prop]) <= 0:
            continue
        if value.startswith("url("):
            raise MergeError(f"The tile content has a {prop} with a paint server.")
        found[prop] = str(elem.style.get("color", "black")) if value == "currentColor" else value
    return found


def shape_copy(elem, transform: inkex.Transform):
    """Return a copy of the shape elem in pattern space. transform goes from elem to
    pattern space.

    Inkscape rounds the coordinates of a boolean operation before it applies the
    transform. Content in small units, for example with objectBoundingBox content
    units, then loses precision. Thus a shape with no stroke gets its transform in
    the path data. A stroke width and dashes do not scale with path data, so a
    shape with a stroke keeps its transform."""
    if str(elem.style.get("stroke", "none")) == "none":
        shape = inkex.PathElement()
        set_raw(shape, "d", fmt_path(elem.path.transform(transform)))
        shape.set("style", str(elem.style))
        return shape
    shape = type(elem)()
    shape.attrib.update({k: v for k, v in elem.attrib.items() if k != "transform"})
    set_raw(shape, "transform", fmt_transform(transform))
    return shape


def content_of(part: Part, reset: bool, viewport: tuple[float, float]) -> Content:
    """Return the painted shapes and colors of the tile content of part. reset and
    viewport go to styled_copy().

    Raise MergeError for content that cannot become one filled path."""
    template = inkex.Group()
    template.set("transform", fmt_transform(part.plan.content))
    for original in part.pattern.content:
        template.append(styled_copy(original, reset, viewport))
    shapes, colors, opacity = [], [], 1.0
    for elem in rendered(template):
        if elem.TAG in UNBOUNDED:
            raise MergeError(f"The tile content has a <{elem.TAG}> element.")
        if any(elem.style.get(p, "none") != "none" for p in NOT_MERGEABLE):
            raise MergeError(
                "The tile content has a filter, a mask, a clip, markers or a vector-effect."
            )
        if not isinstance(elem, SHAPES) or hidden(elem, template):
            continue
        if not (found := paints(elem, template)):
            continue
        shapes.append(shape_copy(elem, transform_within(elem, template)))
        colors += found.values()
        opacity = min(opacity, shown_opacity(elem, template, found))
    return Content(shapes, colors, opacity)


def tile_key(part: Part, tolerance: float):
    """Return a key for the tile path: equal keys give equal tile paths."""
    plan = part.plan
    return (
        id(part.pattern),
        tuple(plan.content.to_hexad()),
        plan.tile,
        overlap_of(plan, tolerance),
    )


def tile_paths(tiles: dict, tolerance: float, frame: dict) -> dict:
    """Return the result of run_all() for each key in tiles: the tile path cut
    to the tile box. tiles maps each key of tile_key() to (part, content)."""
    items = []
    for k, (part, content) in enumerate(tiles.values()):
        t, grow = part.plan.tile, overlap_of(part.plan, tolerance)
        box = black(inkex.PathElement(), f"tile{k}")
        set_raw(
            box, "d", box_path(t.x - grow, t.y - grow, t.x + t.width + grow, t.y + t.height + grow)
        )
        work = group(f"work{k}", group(f"content{k}", *content.shapes), box)
        items.append(Item(work, tile_actions(k), f"content{k}", f"tile{k}"))
    return dict(zip(tiles, run_all(items, frame), strict=True))


def merge_targets(targets: dict, frame: dict) -> dict:
    """Return the result of run_all() for each element in targets, which maps an
    element to (part, tile path): the tiles joined and cut to the element."""
    items = []
    for k, (elem, (part, tile)) in enumerate(targets.items()):
        copies = " ".join(
            fmt_path(tile.transform(tile_transform(part.plan, i, j))) for i, j in part.plan.cells
        )
        tiles = black(inkex.PathElement(), f"tiles{k}")
        set_raw(tiles, "d", copies)
        rule = str(elem.specified_style().get("fill-rule", "nonzero"))
        shape = black(geometry_copy(elem), f"shape{k}", rule)
        work = group(f"target{k}", tiles, shape)
        items.append(Item(work, target_actions(k), f"target{k}", f"shape{k}"))
    return dict(zip(targets, run_all(items, frame), strict=True))


def merge_fills(fills: dict, tolerance: float, reset: bool) -> dict:
    """Return the merged fill of each element in fills, which maps an element to
    its fill Part. Each value is a Merged, None when the fill paints nothing, or
    the MergeError that tells why merged mode cannot make it.

    reset goes to styled_copy(). Elements with the same tile share one content."""
    if not fills:
        return {}
    svg = next(iter(fills)).root
    frame, viewport = frame_of(svg), (svg.viewbox_width, svg.viewbox_height)
    keys = {elem: tile_key(part, tolerance) for elem, part in fills.items()}
    contents: dict = {}
    for elem, key in keys.items():
        if key not in contents:
            try:
                contents[key] = content_of(fills[elem], reset, viewport)
            except MergeError as err:
                contents[key] = err
    tiles = {
        key: (fills[elem], contents[key])
        for elem, key in keys.items()
        if isinstance(contents[key], Content) and contents[key].shapes
    }
    paths = tile_paths(tiles, tolerance, frame) if tiles else {}
    results, targets = {}, {}
    for elem, key in keys.items():
        result = paths.get(key, contents[key])
        if isinstance(result, inkex.Path):
            targets[elem] = (fills[elem], result)
        else:
            # A MergeError, None for an empty tile, or a Content with no shapes.
            results[elem] = None if isinstance(result, Content) else result
    for elem, merged in (merge_targets(targets, frame) if targets else {}).items():
        content = contents[keys[elem]]
        results[elem] = (
            Merged(fmt_path(merged), content) if isinstance(merged, inkex.Path) else merged
        )
    return results


def merged_layer(elem, merged: Merged, reset: bool):
    """Return the fill layer of elem for flatten_element(): a group with the merged path.

    With reset, the path gets the initial value of each property that a stylesheet
    rule could set, as the clipped copies do (see styled_copy())."""
    layer = inkex.Group()
    layer.set("id", unique_id(elem.root, f"{elem.get_id()}-fill"))
    set_props(layer, paint_style(elem.specified_style(), "fill"))
    path = inkex.PathElement()
    set_raw(path, "d", merged.d)
    style = {"fill": merged.content.colors[0], "stroke": "none", "fill-rule": "nonzero"}
    set_props(path, {**RESET_STYLE, **GROUP_PROPS, **style} if reset else style)
    layer.append(path)
    return layer
