"""Clipped-copies output: one copy of the tile content for each tile, clipped
to the tile box and to the fill or stroke area of the target.

The output replaces the target at the same place in the document:

    <g id="ID-flat" transform=... clip-path/mask/filter/opacity/display=...>
      <g id="ID-fill" clip-path="url(#ID-shape)" style="RESET; opacity">
        <g transform="T_ij" [clip-path="url(#ID-tile)"]>  one for each tile
          <g transform="C"> styled copies of the tile content </g>
        </g>
      </g>
      <g id="ID-stroke" mask="url(#ID-stroke-mask)" style="RESET; opacity">
        ... the same, with the clip ID-stroke-tile
      </g>
      <path id="ID" ... fill:none; stroke:none />           the target
    </g>

The fill group is only there for a pattern fill, and the stroke group only for
a pattern stroke. Each group goes at the place that paint-order gives. The tile
clip is only there when the content can show outside the tile box.
"""

from copy import deepcopy
from typing import NamedTuple

import inkex

from fp_resolve import SVG_NS, ResolvedPattern, SkipError
from fp_svg import (
    RESET_STYLE,
    below_one,
    box_path,
    fmt_transform,
    num,
    set_props,
    set_raw,
    take_props,
    unique_id,
)
from fp_tiling import (
    TilePlan,
    box_of,
    resolve_percentages,
    stroke_hull,
    stroke_width,
    tile_transform,
)

# The properties that apply to the target as a whole, so they move to the group,
# with the initial values that stop a stylesheet rule on the target.
GROUP_PROPS = {
    "clip-path": "none",
    "mask": "none",
    "filter": "none",
    "opacity": "1",
    "display": "inline",
}
# Content of these kinds can go past its bounding box, so it always gets the tile clip.
UNBOUNDED = {"text", "flowRoot", "image", "use", "foreignObject", "switch", "svg"}
# The elements that paint their own path. inkex.Polygon is a Polyline.
SHAPES = (
    inkex.PathElement,
    inkex.Rectangle,
    inkex.Circle,
    inkex.Ellipse,
    inkex.Line,
    inkex.Polyline,
)
# The attributes that give the geometry of a shape.
GEOMETRY = {
    "d",
    "x",
    "y",
    "width",
    "height",
    "rx",
    "ry",
    "cx",
    "cy",
    "r",
    "x1",
    "y1",
    "x2",
    "y2",
    "points",
    "pathLength",
}
MARKERS = ("marker-start", "marker-mid", "marker-end")
# Content with one of these properties can paint outside its hull, so it always
# gets the tile clip. A non-scaling stroke has its width in screen units.
EFFECTS = ("filter", "vector-effect", *MARKERS)
# The default paint order.
LAYERS = ("fill", "stroke", "markers")
# The properties that set the stroke area. The stroke mask copies them.
STROKE_SHAPE = (
    "stroke-width",
    "stroke-linecap",
    "stroke-linejoin",
    "stroke-miterlimit",
    "stroke-dasharray",
    "stroke-dashoffset",
)


class Part(NamedTuple):
    """The tiles for one paint of a target."""

    pattern: ResolvedPattern
    plan: TilePlan
    region: inkex.BoundingBox  # the area that the tiles cover: the fill or stroke box


def styled_copy(original, reset: bool, viewport: tuple[float, float]):
    """Copy original. Write the specified style of each element onto its copy.

    Pattern content inherits from the pattern and its ancestors, never from the
    target. The copies move to a new parent, so each copy gets its full style.
    With reset, each copy also gets RESET_STYLE for the properties that are not
    set. Then a stylesheet rule that matches the new place cannot change it.
    Each shape copy gets its percentages as numbers (see resolve_percentages())."""
    copy = deepcopy(original)
    for src, dst in zip(original.iter(), copy.iter(), strict=True):
        if not isinstance(src.tag, str):
            continue  # comments and processing instructions
        dst.attrib.pop("id", None)
        dst.attrib.pop("class", None)  # the style below already holds the class rules
        if src.tag.startswith(SVG_NS):
            style = src.specified_style()
            values = {k: style.get(k) for k in style}
            dst.attrib["style"] = str(inkex.Style({**RESET_STYLE, **values} if reset else values))
        if isinstance(dst, SHAPES):
            resolve_percentages(dst, viewport)
    return copy


def transform_within(elem, top) -> inkex.Transform:
    """Return the transform from the coordinates of elem to the parent of top."""
    transform = inkex.Transform()
    node = elem
    while node is not None:
        # Not node.transform: inkex writes that value back with 6 significant digits.
        transform = inkex.Transform(node.attrib.get("transform")) @ transform
        if node is top:
            break
        node = node.getparent()
    return transform


def needs_tile_clip(template, tile, viewport) -> bool:
    """Return True when the content can show outside the tile box.

    The check uses the painted area of each shape, with its stroke. A clip that
    is not necessary costs more than time: two clipped tiles that meet at an
    edge show a thin light line there in some renderers."""
    points = []
    for elem in template.iter():
        hull = painted_hull(elem, viewport)
        if hull is None:
            return True
        if hull:
            transform = transform_within(elem, template)
            points += [tuple(transform.apply_to_point(p)) for p in hull]
    box = box_of(points)
    return box is not None and not inside(box, tile)


def painted_hull(elem, viewport) -> list | None:
    """Return the hull points of the area that elem paints, in its own coordinates.

    The list is empty when elem paints nothing itself, for example a group. The
    function returns None when the painted area is not known."""
    if not isinstance(elem.tag, str):
        return []  # comments and processing instructions
    if elem.TAG in UNBOUNDED:
        return None
    if any(elem.style.get(p, "none") != "none" for p in EFFECTS):
        return None
    if not isinstance(elem, SHAPES):
        return []
    try:
        return stroke_hull(elem.path, elem.style, viewport)
    except (SkipError, ValueError):
        return None


def inside(box: inkex.BoundingBox, tile) -> bool:
    """Return True when box is inside the tile box, with a small tolerance."""
    margin = 1e-9 * max(tile.width, tile.height)
    return (
        box.left >= tile.x - margin
        and box.top >= tile.y - margin
        and box.right <= tile.x + tile.width + margin
        and box.bottom <= tile.y + tile.height + margin
    )


def add_clip(svg, wanted_id: str, child):
    """Add a clipPath with child to the defs and return the clipPath."""
    clip = inkex.ClipPath()
    clip.set("id", unique_id(svg, wanted_id))
    clip.set("clipPathUnits", "userSpaceOnUse")
    clip.append(child)
    svg.defs.append(clip)
    return clip


def path_element(d: str):
    elem = inkex.PathElement()
    set_raw(elem, "d", d)
    return elem


def geometry_copy(elem):
    """Return a new element with the tag and the geometry attributes of elem only.

    The renderer makes the outline of the copy in the same way as for elem. Thus
    the clip and the stroke mask match elem, and the dashes start at the same point."""
    copy = type(elem)()
    for name in GEOMETRY.intersection(elem.attrib):
        copy.attrib[name] = elem.attrib[name]
    return copy


def tile_clip(svg, wanted_id: str, tile):
    box = box_path(tile.x, tile.y, tile.x + tile.width, tile.y + tile.height)
    return add_clip(svg, wanted_id, path_element(box))


def paint_order(spec) -> list[str]:
    """Return "fill", "stroke" and "markers" in the order that the specified style
    spec paints them."""
    words = str(spec.get("paint-order", "normal")).split()
    first = [w for w in words if w in LAYERS]
    return first + [w for w in LAYERS if w not in first]


def paint_style(spec, prop: str) -> dict:
    """Return the style of the group that holds the tiles of prop ("fill" or "stroke")."""
    style = dict(RESET_STYLE)
    if below_one(opacity := str(spec.get(f"{prop}-opacity", "1"))):
        style["opacity"] = opacity
    if str(spec.get("visibility", "visible")) != "visible":
        # A hidden target paints nothing. RESET_STYLE makes the content visible,
        # so hide the whole group.
        style["display"] = "none"
    return style


def tile_group(svg, group_id: str, clip_id: str, part: Part, reset: bool):
    """Make a group with one copy of the tile content for each tile of part.
    reset goes to styled_copy()."""
    group = inkex.Group()
    group.set("id", unique_id(svg, group_id))
    template = inkex.Group()
    set_raw(template, "transform", fmt_transform(part.plan.content))
    viewport = (svg.viewbox_width, svg.viewbox_height)
    for original in part.pattern.content:
        template.append(styled_copy(original, reset, viewport))
    clip = (
        tile_clip(svg, clip_id, part.plan.tile)
        if needs_tile_clip(template, part.plan.tile, viewport)
        else None
    )

    for i, j in part.plan.cells:
        cell = inkex.Group()
        set_raw(cell, "transform", fmt_transform(tile_transform(part.plan, i, j)))
        if clip is not None:
            cell.clip = clip
        cell.append(deepcopy(template))
        group.append(cell)
    return group


def stroke_mask(svg, eid: str, shape, spec, region: inkex.BoundingBox):
    """Add a mask that shows the stroke area of the target and return the mask.

    The mask holds shape, a geometry copy of the target, with a white stroke. The
    mask is in the defs, where em and % can give another width. Thus the stroke
    width goes into the mask in user units."""
    stroke = {p: str(spec[p]) for p in STROKE_SHAPE if p in spec}
    stroke["stroke-width"] = num(stroke_width(spec, (svg.viewbox_width, svg.viewbox_height)))
    set_props(shape, {**RESET_STYLE, "fill": "none", "stroke": "#ffffff", **stroke})
    mask = inkex.Mask()
    mask.set("id", unique_id(svg, f"{eid}-stroke-mask"))
    mask.set("maskUnits", "userSpaceOnUse")
    mask.set("x", num(region.left))
    mask.set("y", num(region.top))
    mask.set("width", num(region.width))
    mask.set("height", num(region.height))
    mask.append(shape)
    svg.defs.append(mask)
    return mask


def flatten_element(elem, fill: Part | None, stroke: Part | None, ready_fill=None):
    """Replace the pattern fill and the pattern stroke of elem with flat layers.
    fill or stroke is None when that paint stays on elem.

    The fill layer is ready_fill when the caller gives one, for example from
    merged mode. Else each layer holds clipped copies of the tile content."""
    svg = elem.root
    spec = elem.specified_style()
    reset = bool(svg.xpath("//svg:style"))
    group = output_group(elem)
    layers = {}
    if ready_fill is not None:
        layers["fill"] = ready_fill
    elif fill is not None:
        layers["fill"] = fill_layer(elem, spec, fill, reset)
    if stroke is not None:
        layers["stroke"] = stroke_layer(elem, spec, stroke, reset)
    set_props(elem, dict.fromkeys(layers, "none"))

    order = paint_order(spec)
    if (markers := marker_layer(elem, spec, order, layers)) is not None:
        layers["markers"] = markers
    # elem paints its other layers and goes at the place of the first one.
    for layer in order:
        if layer in layers:
            group.append(layers[layer])
        elif elem.getparent() is not group:
            group.append(elem)
    return group


def output_group(elem):
    """Put a new group at the place of elem and return it. The transform and the
    GROUP_PROPS of elem move to the group."""
    group = inkex.Group()
    group.set("id", unique_id(elem.root, f"{elem.get_id()}-flat"))
    elem.addprevious(group)
    set_raw(group, "transform", elem.attrib.pop("transform", None))
    set_props(group, take_props(elem, GROUP_PROPS))
    return group


def fill_layer(elem, spec, part: Part, reset: bool):
    """Return the tiles of the fill, clipped to the shape of elem."""
    svg, eid = elem.root, elem.get_id()
    tiles = tile_group(svg, f"{eid}-fill", f"{eid}-tile", part, reset)
    shape = geometry_copy(elem)
    set_props(shape, {"clip-rule": str(spec.get("fill-rule", "nonzero"))})
    tiles.clip = add_clip(svg, f"{eid}-shape", shape)
    set_props(tiles, paint_style(spec, "fill"))
    return tiles


def stroke_layer(elem, spec, part: Part, reset: bool):
    """Return the tiles of the stroke, masked to the stroke area of elem."""
    svg, eid = elem.root, elem.get_id()
    tiles = tile_group(svg, f"{eid}-stroke", f"{eid}-stroke-tile", part, reset)
    mask = stroke_mask(svg, eid, geometry_copy(elem), spec, part.region)
    set_props(tiles, {**paint_style(spec, "stroke"), "mask": f"url(#{mask.get_id()})"})
    return tiles


def marker_layer(elem, spec, order: list[str], layers: dict):
    """Return a copy of elem with only its markers, or None when elem can keep them.

    elem goes at the place of its first own layer. When a pattern layer paints
    between that place and the markers, the markers must move to the copy."""
    own = order.index(next(layer for layer in order if layer not in layers))
    between = any(own < order.index(layer) < order.index("markers") for layer in layers)
    if not between or all(spec.get(m, "none") == "none" for m in MARKERS):
        return None
    markers = geometry_copy(elem)
    markers.set("id", unique_id(elem.root, f"{elem.get_id()}-markers"))
    # stroke-width scales markers with markerUnits="strokeWidth".
    shown = {p: str(spec[p]) for p in (*MARKERS, "stroke-width") if p in spec}
    set_props(markers, {"fill": "none", "stroke": "none", **shown})
    set_props(elem, dict.fromkeys(MARKERS, "none"))
    return markers
