"""Clipped-copies output: one copy of the tile content for each tile, clipped
to the tile box and to the shape of the target.

The output replaces the target at the same place in the document:

    <g id="ID-flat" transform=... clip-path/mask/filter/opacity/display=...>
      <g id="ID-fill" clip-path="url(#ID-shape)" style="RESET; opacity">
        <g transform="T_ij" [clip-path="url(#ID-tile)"]>  one for each tile
          <g transform="C"> styled copies of the tile content </g>
        </g>
      </g>
      <path id="ID" ... fill:none />                         the target
    </g>

The tile clip is only there when the content can show outside the tile box.
"""

from copy import deepcopy

import inkex

from fp_resolve import SVG_NS, ResolvedPattern
from fp_svg import (
    RESET_STYLE,
    fmt_path,
    fmt_transform,
    num,
    set_props,
    set_raw,
    take_props,
    unique_id,
)
from fp_tiling import TilePlan, tile_transform

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
UNBOUNDED = {"text", "flowRoot", "image", "use", "foreignObject", "switch"}
MARKERS = ("marker-start", "marker-mid", "marker-end")


def styled_copy(original, reset: bool):
    """Copy original. Write the specified style of each element onto its copy.

    Pattern content inherits from the pattern and its ancestors, never from the
    target. The copies move to a new parent, so each copy gets its full style.
    With reset, each copy also gets RESET_STYLE for the properties that are not
    set. Then a stylesheet rule that matches the new place cannot change it."""
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
    return copy


def needs_tile_clip(template, tile) -> bool:
    """Return True when the content can show outside the tile box."""
    for elem in template.iterdescendants():
        if not isinstance(elem.tag, str):
            continue
        if elem.TAG in UNBOUNDED:
            return True
        style = elem.style
        # The bounding box does not include strokes, markers or filter effects.
        if any(style.get(p, "none") != "none" for p in ("stroke", "filter", *MARKERS)):
            return True
    box = template.bounding_box()
    if box is None:
        return False
    margin = 1e-9 * max(tile.width, tile.height)
    return (
        box.left < tile.x - margin
        or box.top < tile.y - margin
        or box.right > tile.x + tile.width + margin
        or box.bottom > tile.y + tile.height + margin
    )


def stroke_first(elem) -> bool:
    """Return True when paint-order paints the stroke before the fill."""
    order = str(elem.specified_style().get("paint-order", "normal")).split()
    if "stroke" not in order:
        return False
    return "fill" not in order or order.index("stroke") < order.index("fill")


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


def tile_clip(svg, wanted_id: str, tile):
    x0, y0, x1, y1 = tile.x, tile.y, tile.x + tile.width, tile.y + tile.height
    box = path_element(f"M {num(x0)} {num(y0)} H {num(x1)} V {num(y1)} H {num(x0)} Z")
    return add_clip(svg, wanted_id, box)


def flatten_fill(elem, rp: ResolvedPattern, tile_plan: TilePlan):
    """Replace the pattern fill of elem with clipped copies of the tile content."""
    svg = elem.root
    eid = elem.get_id()
    spec = elem.specified_style()

    group = inkex.Group()
    group.set("id", unique_id(svg, f"{eid}-flat"))
    elem.addprevious(group)
    set_raw(group, "transform", elem.attrib.pop("transform", None))
    set_props(group, take_props(elem, GROUP_PROPS))

    shape = path_element(fmt_path(elem.path))
    set_props(shape, {"clip-rule": str(spec.get("fill-rule", "nonzero"))})
    fill = inkex.Group()
    fill.set("id", unique_id(svg, f"{eid}-fill"))
    fill.clip = add_clip(svg, f"{eid}-shape", shape)
    fill_style = dict(RESET_STYLE)
    if (opacity := str(spec.get("fill-opacity", "1"))) not in ("1", "1.0"):
        fill_style["opacity"] = opacity
    if str(spec.get("visibility", "visible")) != "visible":
        # A hidden target paints no fill. RESET_STYLE makes the content visible,
        # so hide the whole fill group.
        fill_style["display"] = "none"
    set_props(fill, fill_style)
    group.append(fill)

    template = inkex.Group()
    set_raw(template, "transform", fmt_transform(tile_plan.content))
    has_stylesheet = bool(svg.xpath("//svg:style"))
    for original in rp.content:
        template.append(styled_copy(original, reset=has_stylesheet))
    # bounding_box() needs the template in the document, so add it for the check only.
    fill.append(template)
    clip = (
        tile_clip(svg, f"{eid}-tile", tile_plan.tile)
        if needs_tile_clip(template, tile_plan.tile)
        else None
    )
    fill.remove(template)

    for i, j in tile_plan.cells:
        cell = inkex.Group()
        set_raw(cell, "transform", fmt_transform(tile_transform(tile_plan, i, j)))
        if clip is not None:
            cell.clip = clip
        cell.append(deepcopy(template))
        fill.append(cell)

    set_props(elem, {"fill": "none"})
    if stroke_first(elem):
        group.insert(0, elem)  # the stroke paints below the fill
    else:
        group.append(elem)
    return group
