"""Tiling math: the tile box, the content transform and the tiles to place.

All values are in the user space of the target element. That space includes
the transform attribute of the element itself.
"""

import itertools
import math
from typing import NamedTuple

import inkex
from inkex.units import CONVERSIONS

from fp_resolve import Length, ResolvedPattern, SkipError, parse_length

# CSS default font size. The extension does not read the font of the pattern.
FONT_UNITS = {"em": 16.0, "ex": 8.0}
# A relative tolerance: a row overlap below this part of the tile area, or a region
# side below this part of the other side, counts as zero.
EPS = 1e-9


class TileLimitError(ValueError):
    """The target needs more tiles than the limit.

    The argument is a lower bound of the tile count."""


class Tile(NamedTuple):
    x: float
    y: float
    width: float
    height: float


class TilePlan(NamedTuple):
    tile: Tile  # the tile box in pattern space
    content: inkex.Transform  # C: from content coordinates to pattern space
    transform: inkex.Transform  # P: patternTransform, kept here for tile_transform()
    cells: list[tuple[int, int]]  # the (i, j) index of each tile to place


def to_user(length: Length, units: str, size: float, viewport: float) -> float:
    """Convert a pattern length to a user-space distance.

    With objectBoundingBox units, the value is a fraction of size (the bounding
    box side). With userSpaceOnUse, a percentage refers to the viewport side."""
    obb = units == "objectBoundingBox"
    value, unit = length
    if unit == "%":
        return value / 100 * (size if obb else viewport)
    value *= FONT_UNITS.get(unit) or CONVERSIONS[unit]  # CONVERSIONS has "" and px as 1
    return value * size if obb else value


def tile_box(
    rp: ResolvedPattern,
    bbox: inkex.BoundingBox,
    viewport: tuple[float, float],
    inkscape_obb: bool = False,
):
    """Return the tile box in pattern space, or None when the pattern paints nothing.

    With objectBoundingBox units, the tile grid starts at the corner of the bounding
    box. Inkscape 1.4 does not add the corner, against the SVG standard. With
    inkscape_obb, this function does the same as Inkscape."""
    obb = rp.units == "objectBoundingBox"
    ox, oy = (bbox.left, bbox.top) if obb and not inkscape_obb else (0.0, 0.0)

    def horizontal(length):
        return to_user(length, rp.units, bbox.width, viewport[0])

    def vertical(length):
        return to_user(length, rp.units, bbox.height, viewport[1])

    tile = Tile(
        ox + horizontal(rp.x), oy + vertical(rp.y), horizontal(rp.width), vertical(rp.height)
    )
    if tile.width < 0 or tile.height < 0:
        raise SkipError("The pattern has a negative width or height.")
    # Only an exact zero paints nothing. A very small tile gives a large tile count,
    # and the tile limit in plan() stops it.
    return None if tile.width == 0 or tile.height == 0 else tile


def viewbox_transform(viewbox, width: float, height: float, aspect: str) -> inkex.Transform:
    """Map viewbox into the box (0, 0, width, height) with preserveAspectRatio."""
    vx, vy, vw, vh = viewbox
    sx, sy = width / vw, height / vh
    words = aspect.removeprefix("defer").split()
    align = words[0]
    if align == "none":
        return inkex.Transform(scale=(sx, sy)) @ inkex.Transform(translate=(-vx, -vy))
    s = max(sx, sy) if words[-1] == "slice" else min(sx, sy)
    shift = {"Min": 0.0, "Mid": 0.5, "Max": 1.0}
    dx = shift[align[1:4]] * (width - vw * s)
    dy = shift[align[5:8]] * (height - vh * s)
    # Transform(translate=..., scale=...) scales the translation too, so compose.
    return inkex.Transform(translate=(dx - vx * s, dy - vy * s)) @ inkex.Transform(scale=s)


def content_transform(rp: ResolvedPattern, tile: Tile, bbox: inkex.BoundingBox):
    """Return C, the transform from content coordinates to pattern space, or None
    when the pattern paints nothing."""
    origin = inkex.Transform(translate=(tile.x, tile.y))
    if rp.viewbox is not None:
        vw, vh = rp.viewbox[2:]
        if vw < 0 or vh < 0:
            raise SkipError("The pattern has a viewBox with a negative width or height.")
        if vw == 0 or vh == 0:
            return None
        return origin @ viewbox_transform(rp.viewbox, tile.width, tile.height, rp.aspect)
    if rp.content_units == "objectBoundingBox":
        return origin @ inkex.Transform(scale=(bbox.width, bbox.height))
    return origin


def inkscape_view_differs(rp: ResolvedPattern, tile: Tile, bbox: inkex.BoundingBox) -> bool:
    """Return True when Inkscape 1.4 shows the pattern at another place than the
    SVG standard.

    With objectBoundingBox units, Inkscape does not move the tile grid to the corner
    of the bounding box. The views differ when that corner is not a whole number
    of tiles from the origin."""

    def off_grid(value: float, size: float) -> bool:
        # The float error of remainder() grows with value, so the tolerance does too.
        return abs(math.remainder(value, size)) > EPS * max(size, abs(value))

    return rp.units == "objectBoundingBox" and (
        off_grid(bbox.left, tile.width) or off_grid(bbox.top, tile.height)
    )


def stroke_width(style, viewport: tuple[float, float]) -> float:
    """Return the stroke width of style in user units.

    A percentage refers to the normalized diagonal of the root viewport. em and
    ex refer to the font-size of style."""
    text = str(style.get("stroke-width", "1")).strip()
    try:
        length = parse_length(text)
    except ValueError:
        raise SkipError(f"The stroke width {text!r} is not a valid length.") from None
    if length.value < 0:
        raise SkipError(f"The stroke width {text!r} is negative.")
    if length.unit in FONT_UNITS:
        return length.value * font_size(style) * FONT_UNITS[length.unit] / FONT_UNITS["em"]
    diagonal = math.hypot(*viewport) / math.sqrt(2)
    return to_user(length, "userSpaceOnUse", diagonal, diagonal)


def font_size(style) -> float:
    """Return the font-size of style in user units.

    A relative font-size (%, em, ex or a keyword) gives the CSS default, 16."""
    try:
        length = parse_length(str(style.get("font-size", "16")).strip())
    except ValueError:
        return FONT_UNITS["em"]
    if length.unit in ("%", *FONT_UNITS):
        return FONT_UNITS["em"]
    return to_user(length, "userSpaceOnUse", 0, 0)


def stroke_region(path: inkex.Path, style, viewport) -> inkex.BoundingBox | None:
    """Return a box that holds the stroke of path, or None when the stroke paints
    nothing (a zero width, or a path with no segments). See stroke_hull()."""
    if stroke_width(style, viewport) == 0:
        return None
    return box_of(stroke_hull(path, style, viewport))


def box_of(points) -> inkex.BoundingBox | None:
    """Return the bounding box of points, or None when there are no points."""
    if not points:
        return None
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return inkex.BoundingBox((min(xs), max(xs)), (min(ys), max(ys)))


def cap_reach(style) -> float:
    """Return how far a line cap goes past the end of the path, in half stroke widths."""
    return {"round": 1.0, "square": math.sqrt(2)}.get(str(style.get("stroke-linecap", "butt")), 0.0)


def join_reach(style) -> float:
    """Return how far a join can go from its vertex, in half stroke widths."""
    if str(style.get("stroke-linejoin", "miter")) not in ("miter", "miter-clip", "arcs"):
        return 1.0  # round and bevel joins stay inside a circle around the vertex
    try:
        return max(1.0, float(style.get("stroke-miterlimit", "4")))
    except ValueError:
        return 4.0


def stroke_hull(path: inkex.Path, style, viewport) -> list[tuple[float, float]]:
    """Return points in the coordinates of path. Their bounding box holds the
    painted area of path: the fill area, and the stroke when stroke is not none.

    A straight segment gives the exact corners of its stroke. Curves, joins and
    caps give a larger box, so the result can be larger than the stroke."""
    stroked = str(style.get("stroke", "none")) != "none"
    half = stroke_width(style, viewport) / 2 if stroked else 0.0
    dashed = str(style.get("stroke-dasharray", "none")) not in ("none", "")
    cap, join = half * cap_reach(style), half * join_reach(style)
    return [
        point for sub in path.to_superpath() for point in subpath_hull(sub, half, cap, join, dashed)
    ]


def square(p, r: float) -> list[tuple[float, float]]:
    """Return the corners of the square with the center p and the half side r."""
    return [(p[0] + dx, p[1] + dy) for dx in (-r, r) for dy in (-r, r)]


def subpath_hull(sub, half: float, cap: float, join: float, dashed: bool) -> list:
    """Return the hull points of one subpath of a superpath (see stroke_hull()).

    half is half the stroke width. cap and join are the reach of the caps and joins."""
    points = []
    for a, b in itertools.pairwise(sub):
        controls = (a[1], a[2], b[0], b[1])
        points += segment_hull(*controls, half)
        if dashed:
            # Each dash has caps, at any place along the segment.
            points += [q for p in controls for q in square(p, cap)]
    points += [q for node in sub[1:-1] for q in square(node[1], join)]
    # A closed subpath has a join at its start, an open one has caps.
    ends = [sub[0][1], sub[-1][1]]
    reach = max(cap, join if ends[0] == ends[1] else 0.0)
    return points + [q for p in ends for q in square(p, reach)]


def segment_hull(p0, c1, c2, p3, half: float) -> list:
    """Return points whose bounding box holds the stroke of one cubic segment, without
    caps and joins. A straight segment gives the exact corners of its stroke."""
    length = math.dist(p0, p3)
    if c1 == p0 and c2 == p3 and length > 0:
        nx, ny = (p0[1] - p3[1]) / length * half, (p3[0] - p0[0]) / length * half
        return [(p[0] + s * nx, p[1] + s * ny) for p in (p0, p3) for s in (-1, 1)]
    # A curve stays inside the hull of its control points.
    return [q for p in (p0, c1, c2, p3) for q in square(p, half)]


def corners(box: inkex.BoundingBox) -> list[tuple[float, float]]:
    return [
        (box.left, box.top),
        (box.right, box.top),
        (box.right, box.bottom),
        (box.left, box.bottom),
    ]


def span(low: float, high: float, origin: float, size: float) -> range:
    """Return the indexes k of the intervals [origin + k*size, origin + (k+1)*size]
    that overlap [low, high]."""
    return range(math.floor((low - origin) / size), math.ceil((high - origin) / size))


def clip(poly, value: float, keep_above: bool):
    """Clip the polygon to the half plane y >= value (keep_above) or y <= value."""
    sign = 1 if keep_above else -1
    inside = [sign * (p[1] - value) >= 0 for p in poly]
    out = []
    for k, cur in enumerate(poly):
        prev = poly[k - 1]
        cur_in, prev_in = inside[k], inside[k - 1]
        if cur_in != prev_in:
            t = (value - prev[1]) / (cur[1] - prev[1])
            out.append((prev[0] + t * (cur[0] - prev[0]), value))
        if cur_in:
            out.append(cur)
    return out


def area(poly) -> float:
    pairs = zip(poly, poly[1:] + poly[:1], strict=True)
    return 0.5 * abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in pairs))


def cells(tile: Tile, transform: inkex.Transform, region: inkex.BoundingBox):
    """Yield the (i, j) index of each tile that shares an area with region, row by row.

    In pattern space, region is a parallelogram. For each tile row, the part of the
    parallelogram inside the row is convex, so its u range gives the exact tiles."""
    inverse = -transform
    shape = [tuple(inverse.apply_to_point(p)) for p in corners(region)]
    ys = [p[1] for p in shape]
    for j in span(min(ys), max(ys), tile.y, tile.height):
        top = tile.y + j * tile.height
        part = clip(clip(shape, top, True), top + tile.height, False)
        if area(part) <= EPS * tile.width * tile.height:
            continue  # the row only touches the region
        xs = [p[0] for p in part]
        margin = EPS * tile.width
        for i in span(min(xs) + margin, max(xs) - margin, tile.x, tile.width):
            yield i, j


def plan(
    rp: ResolvedPattern, bbox, region, viewport, limit: int, inkscape_obb: bool = False
) -> TilePlan | None:
    """Make the tile plan for one target, or None when the pattern paints nothing.

    bbox is the geometric bounding box of the target. It sets the objectBoundingBox
    units. region is the box to cover: bbox for a fill, the stroke box for a stroke.
    If the target needs more than limit tiles, the function raises TileLimitError
    before or while it finds the tiles."""
    tile = tile_box(rp, bbox, viewport, inkscape_obb)
    if tile is None:
        return None
    content = content_transform(rp, tile, bbox)
    if content is None or has_no_area(rp.transform, region):
        return None
    return TilePlan(tile, content, rp.transform, find_cells(tile, rp.transform, region, limit))


def area_scale(transform: inkex.Transform) -> float:
    """Return the factor by which transform multiplies an area."""
    return abs(transform.a * transform.d - transform.b * transform.c)


def has_no_area(transform: inkex.Transform, region: inkex.BoundingBox) -> bool:
    """Return True when the patternTransform or the region has no area. Then the
    pattern paints nothing."""
    w, h = region.width, region.height
    # A region with no area (for example the box of a straight line) also gives
    # no lower bound in find_cells(), so cells() could scan forever.
    return area_scale(transform) == 0 or min(w, h) <= EPS * max(w, h)


def find_cells(tile: Tile, transform, region, limit: int) -> list[tuple[int, int]]:
    """Return the cells that cover region. Raise TileLimitError when there are
    more than limit, before the scan when the area alone gives too many."""
    # The tiles cover the region, so their count is at least this. A very small
    # tile or patternTransform gives a very large count, so cap it before ceil().
    tile_area = area_scale(transform) * tile.width * tile.height
    least = math.ceil(min(region.width * region.height / tile_area, 1e18) - EPS)
    if least > limit:
        raise TileLimitError(least)
    found = list(itertools.islice(cells(tile, transform, region), limit + 1))
    if len(found) > limit:
        raise TileLimitError(len(found))
    return found


def tile_transform(tile_plan: TilePlan, i: int, j: int) -> inkex.Transform:
    """Return T_ij, the transform from pattern space of tile (0, 0) to tile (i, j) in user space."""
    tile = tile_plan.tile
    return tile_plan.transform @ inkex.Transform(translate=(i * tile.width, j * tile.height))
