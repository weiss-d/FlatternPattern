"""Tiling math: the tile box, the content transform and the tiles to place.

All values are in the user space of the target element. That space includes
the transform attribute of the element itself.
"""

import itertools
import math
from typing import NamedTuple

import inkex
from inkex.units import CONVERSIONS

from fp_resolve import Length, ResolvedPattern, SkipError

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


def tile_box(rp: ResolvedPattern, bbox: inkex.BoundingBox, viewport: tuple[float, float]):
    """Return the tile box in pattern space, or None when the pattern paints nothing."""
    obb = rp.units == "objectBoundingBox"
    ox, oy = (bbox.left, bbox.top) if obb else (0.0, 0.0)

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


def plan(rp: ResolvedPattern, bbox, region, viewport, limit: int) -> TilePlan | None:
    """Make the tile plan for one target, or None when the pattern paints nothing.

    bbox is the geometric bounding box of the target. It sets the objectBoundingBox
    units. region is the box to cover: bbox for a fill, the stroke box for a stroke.
    If the target needs more than limit tiles, the function raises TileLimitError
    before or while it finds the tiles."""
    tile = tile_box(rp, bbox, viewport)
    if tile is None:
        return None
    content = content_transform(rp, tile, bbox)
    if content is None:
        return None
    t = rp.transform
    det = abs(t.a * t.d - t.b * t.c)
    if det == 0:
        return None  # a patternTransform with no area paints nothing
    w, h = region.width, region.height
    if min(w, h) <= EPS * max(w, h):
        # A region with no area (for example the box of a straight line) shows no
        # paint. It also gives no lower bound below, so cells() could scan forever.
        return None
    # The tiles cover the region, so their count is at least this. A very small
    # tile or patternTransform gives a very large count, so cap it before ceil().
    least = math.ceil(min(w * h / (det * tile.width * tile.height), 1e18) - EPS)
    if least > limit:
        raise TileLimitError(least)
    found = list(itertools.islice(cells(tile, t, region), limit + 1))
    if len(found) > limit:
        raise TileLimitError(len(found))
    return TilePlan(tile, content, t, found)


def tile_transform(tile_plan: TilePlan, i: int, j: int) -> inkex.Transform:
    """Return T_ij, the transform from pattern space of tile (0, 0) to tile (i, j) in user space."""
    tile = tile_plan.tile
    return tile_plan.transform @ inkex.Transform(translate=(i * tile.width, j * tile.height))
