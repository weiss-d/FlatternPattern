import math
import random

import inkex
import pytest

import fp_tiling as ft
from fp_resolve import ResolvedPattern, SkipError, parse_length

VIEWPORT = (200.0, 100.0)
BBOX = inkex.BoundingBox((10, 50), (20, 40))  # x 10..50, y 20..40


def rp(**kw):
    values = {
        "ids": ("p",),
        "content": [],
        "x": "0",
        "y": "0",
        "width": "10",
        "height": "10",
        "units": "userSpaceOnUse",
        "content_units": "userSpaceOnUse",
        "transform": inkex.Transform(),
        "viewbox": None,
        "aspect": "xMidYMid meet",
    }
    values.update(kw)
    for name in ("x", "y", "width", "height"):
        values[name] = parse_length(values[name])
    return ResolvedPattern(**values)


def close(a, b):
    return all(math.isclose(x, y, abs_tol=1e-9) for x, y in zip(a, b, strict=True))


# Tile box


@pytest.mark.parametrize(
    ("kw", "expected"),
    [
        ({"x": "3", "y": "4", "width": "41", "height": "71"}, (3, 4, 41, 71)),
        ({"x": "10%", "width": "50%", "height": "25%"}, (20, 0, 100, 25)),
        ({"width": "10mm", "height": "1in"}, (0, 0, 37.79527559055118, 96)),
        ({"width": "2em", "height": "1ex"}, (0, 0, 32, 8)),
        # objectBoundingBox: fractions or percentages of the bounding box
        (
            {"units": "objectBoundingBox", "x": "0.5", "width": "0.25", "height": "50%"},
            (30, 20, 10, 10),
        ),
    ],
)
def test_tile_box(kw, expected):
    assert close(ft.tile_box(rp(**kw), BBOX, VIEWPORT), expected)


def test_inkscape_view_leaves_out_bbox_corner():
    obb = rp(units="objectBoundingBox", x="0.5", width="0.25", height="50%")
    assert close(ft.tile_box(obb, BBOX, VIEWPORT, inkscape_obb=True), (20, 0, 10, 10))
    # userSpaceOnUse tiles are the same in both views.
    assert ft.tile_box(rp(), BBOX, VIEWPORT, inkscape_obb=True) == ft.tile_box(rp(), BBOX, VIEWPORT)


@pytest.mark.parametrize(
    ("units", "box", "differs"),
    [
        # The tile is 10 x 10 in all rows.
        ("objectBoundingBox", BBOX, False),  # corner (10, 20): whole tiles from the origin
        ("objectBoundingBox", inkex.BoundingBox((13, 53), (20, 40)), True),  # 13 is not
        ("objectBoundingBox", inkex.BoundingBox((0, 40), (0, 20)), False),
        ("userSpaceOnUse", inkex.BoundingBox((13, 53), (20, 40)), False),
    ],
)
def test_inkscape_view_differs(units, box, differs):
    obb = units == "objectBoundingBox"
    pattern = rp(units=units, width="0.25" if obb else "10", height="0.5" if obb else "10")
    tile = ft.tile_box(pattern, box, VIEWPORT)
    assert ft.inkscape_view_differs(pattern, tile, box) is differs


def test_inkscape_view_differs_ignores_float_error_at_large_coordinates():
    # 123456789 tiles of 0.1 from the origin: on the grid, but remainder() gives
    # 3.1e-10. A tolerance of EPS * tile size (1e-10) calls it off the grid.
    box = inkex.BoundingBox((12345678.9, 12345679.3), (0, 0.4))
    pattern = rp(units="objectBoundingBox", width="0.25", height="0.25")
    tile = ft.tile_box(pattern, box, VIEWPORT)
    assert ft.inkscape_view_differs(pattern, tile, box) is False


def test_zero_size_paints_nothing():
    assert ft.tile_box(rp(width="0"), BBOX, VIEWPORT) is None
    flat = inkex.BoundingBox((0, 10), (5, 5))
    assert ft.tile_box(rp(units="objectBoundingBox", width="1", height="1"), flat, VIEWPORT) is None


def test_negative_size_is_an_error():
    with pytest.raises(SkipError, match="negative"):
        ft.tile_box(rp(height="-1"), BBOX, VIEWPORT)


# Content transform


def apply(t, p):
    return tuple(t.apply_to_point(p))


def test_content_transform_moves_to_tile_origin():
    tile = ft.Tile(3, 4, 10, 10)
    c = ft.content_transform(rp(), tile, BBOX)
    assert close(apply(c, (1, 1)), (4, 5))


def test_content_units_object_bounding_box():
    tile = ft.Tile(3, 4, 10, 10)
    c = ft.content_transform(rp(content_units="objectBoundingBox"), tile, BBOX)
    assert close(apply(c, (1, 1)), (3 + 40, 4 + 20))


@pytest.mark.parametrize(
    ("aspect", "p0", "p1"),
    [
        # viewBox 5 5 10 20 into a 40 x 40 tile at (100, 200)
        ("none", (100, 200), (140, 240)),
        ("xMidYMid meet", (110, 200), (130, 240)),  # scale 2, centered on x
        ("xMinYMin meet", (100, 200), (120, 240)),
        ("xMaxYMax meet", (120, 200), (140, 240)),
        ("xMidYMid slice", (100, 180), (140, 260)),  # scale 4, centered on y
        ("defer xMinYMax slice", (100, 160), (140, 240)),
    ],
)
def test_viewbox_aspect(aspect, p0, p1):
    tile = ft.Tile(100, 200, 40, 40)
    c = ft.content_transform(rp(viewbox=(5, 5, 10, 20), aspect=aspect), tile, BBOX)
    assert close(apply(c, (5, 5)), p0)
    assert close(apply(c, (15, 25)), p1)


def test_viewbox_beats_content_units():
    tile = ft.Tile(0, 0, 10, 10)
    pattern = rp(viewbox=(0, 0, 10, 10), content_units="objectBoundingBox")
    assert ft.content_transform(pattern, tile, BBOX) == inkex.Transform()


def test_viewbox_zero_and_negative():
    tile = ft.Tile(0, 0, 10, 10)
    assert ft.content_transform(rp(viewbox=(0, 0, 0, 5)), tile, BBOX) is None
    with pytest.raises(SkipError, match="viewBox"):
        ft.content_transform(rp(viewbox=(0, 0, -1, 5)), tile, BBOX)


# Tile cells


def cell_quad(tile, transform, i, j):
    """The four corners of tile (i, j) in user space."""
    x0, y0 = tile.x + i * tile.width, tile.y + j * tile.height
    x1, y1 = x0 + tile.width, y0 + tile.height
    return [tuple(transform.apply_to_point(p)) for p in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]


def clip_area(quad, box):
    """Area of quad inside box (Sutherland-Hodgman), as an independent check."""
    poly = list(quad)
    # (edge value, axis, sign): a point is inside when sign * (p[axis] - value) >= 0
    edges = [(box.left, 0, 1), (box.right, 0, -1), (box.top, 1, 1), (box.bottom, 1, -1)]
    for value, axis, sign in edges:
        inside = [sign * (p[axis] - value) >= 0 for p in poly]
        out = []
        for k, cur in enumerate(poly):
            prev = poly[k - 1]
            if inside[k] != inside[k - 1]:
                t = (value - prev[axis]) / (cur[axis] - prev[axis])
                hit = (prev[0] + t * (cur[0] - prev[0]), prev[1] + t * (cur[1] - prev[1]))
                out.append(hit)
            if inside[k]:
                out.append(cur)
        poly = out
        if not poly:
            return 0.0
    return 0.5 * abs(
        sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(poly, poly[1:] + poly[:1], strict=True))
    )


def random_transform(rng):
    return inkex.Transform(
        translate=(rng.uniform(-50, 50), rng.uniform(-50, 50)),
        rotate=rng.uniform(0, 360),
        scale=(rng.uniform(0.5, 2), rng.uniform(0.5, 2)),
    ) @ inkex.Transform(skewx=rng.uniform(-30, 30))


@pytest.mark.parametrize("seed", range(25))
def test_cells_match_brute_force(seed):
    rng = random.Random(seed)
    tile = ft.Tile(rng.uniform(-5, 5), rng.uniform(-5, 5), rng.uniform(4, 15), rng.uniform(4, 15))
    transform = random_transform(rng)
    x0, y0 = rng.uniform(-30, 30), rng.uniform(-30, 30)
    region = inkex.BoundingBox((x0, x0 + rng.uniform(5, 60)), (y0, y0 + rng.uniform(5, 60)))
    expected = {
        (i, j)
        for i in range(-40, 40)
        for j in range(-40, 40)
        if clip_area(cell_quad(tile, transform, i, j), region) > 1e-6
    }
    found = list(ft.cells(tile, transform, region))
    assert len(found) == len(set(found))
    assert set(found) == expected


def test_reference_transform_cells_cover_region():
    # The patternTransform of the asanoha reference file.
    transform = inkex.Transform("matrix(1.849 0.393 -0.393 1.849 -12.49 -1962.581)")
    tile = ft.Tile(0, 0, 41, 71)
    region = inkex.BoundingBox((0, 300), (-200, 100))
    found = list(ft.cells(tile, transform, region))
    assert len(found) == len(set(found)) > 0
    assert sum(clip_area(cell_quad(tile, transform, i, j), region) for i, j in found) == (
        pytest.approx(300 * 300)
    )


# Plan


def test_plan_counts_and_transforms():
    tp = ft.plan(rp(), BBOX, BBOX, VIEWPORT, limit=100)
    assert sorted(tp.cells) == [(i, j) for i in range(1, 5) for j in range(2, 4)]
    assert close(apply(ft.tile_transform(tp, 2, 3), (1, 1)), (21, 31))


def test_plan_tile_limit():
    with pytest.raises(ft.TileLimitError) as err:
        ft.plan(rp(), BBOX, BBOX, VIEWPORT, limit=7)
    assert err.value.args[0] == 8
    assert ft.plan(rp(), BBOX, BBOX, VIEWPORT, limit=8) is not None


def test_plan_rejects_huge_region_before_any_scan(monkeypatch):
    monkeypatch.setattr(ft, "cells", lambda *a: pytest.fail("cells() must not run"))
    huge = inkex.BoundingBox((0, 1e6), (0, 1e6))
    with pytest.raises(ft.TileLimitError) as err:
        ft.plan(rp(width="1", height="1"), huge, huge, VIEWPORT, limit=2000)
    assert err.value.args[0] == 10**12


def test_plan_thin_rotated_region_scans_few_rows(monkeypatch):
    # A long thin stroke box under a 45 degree pattern: the bounding window of
    # the tiles is about 2 million cells, but only the rows on the diagonal count.
    calls = []
    clip = ft.clip
    monkeypatch.setattr(ft, "clip", lambda *a: calls.append(1) or clip(*a))
    thin = inkex.BoundingBox((0, 20000), (0, 10))
    pattern = rp(transform=inkex.Transform(rotate=45))
    with pytest.raises(ft.TileLimitError):
        ft.plan(pattern, thin, thin, VIEWPORT, limit=2000)
    # Each row calls clip() two times. Only the first and the last row can give no
    # tile, and the scan stops at limit + 1 tiles.
    assert len(calls) <= 2 * (2000 + 1 + 2)


@pytest.mark.parametrize(
    "region",
    [
        inkex.BoundingBox((5, 5), (0, 1e5)),  # the box of a vertical straight line
        inkex.BoundingBox((0, 1e9), (0, 1e-12)),  # a very thin box
    ],
)
def test_plan_region_without_area_paints_nothing(monkeypatch, region):
    monkeypatch.setattr(ft, "cells", lambda *a: pytest.fail("cells() must not run"))
    pattern = rp(width="0.01", height="0.01", transform=inkex.Transform(rotate=30))
    assert ft.plan(pattern, BBOX, region, VIEWPORT, limit=2000) is None


def test_plan_small_values_are_not_zero():
    # A very small patternTransform, tile or viewBox is valid. It gives many tiles.
    with pytest.raises(ft.TileLimitError):
        ft.plan(rp(transform=inkex.Transform(scale=3e-5)), BBOX, BBOX, VIEWPORT, limit=2000)
    with pytest.raises(ft.TileLimitError):
        ft.plan(rp(width="5e-10"), BBOX, BBOX, VIEWPORT, limit=2000)
    tiny_viewbox = rp(viewbox=(0, 0, 1e-12, 1e-12))
    assert ft.plan(tiny_viewbox, BBOX, BBOX, VIEWPORT, limit=2000) is not None


def test_plan_singular_transform_paints_nothing():
    flat = rp(transform=inkex.Transform(scale=(1, 0)))
    assert ft.plan(flat, BBOX, BBOX, VIEWPORT, limit=100) is None


# Stroke area


@pytest.mark.parametrize(
    ("width", "expected"),
    [("3", 3.0), ("3px", 3.0), ("2mm", 7.559055), ("10%", 15.811388)],
)
def test_stroke_width_in_user_units(width, expected):
    # 10 % of the normalized diagonal of 200 x 100: hypot(200, 100) / sqrt(2) / 10.
    assert ft.stroke_width(inkex.Style(stroke_width=width), VIEWPORT) == pytest.approx(expected)


@pytest.mark.parametrize("width", ["abc", "-1"])
def test_stroke_width_invalid_skips(width):
    with pytest.raises(SkipError):
        ft.stroke_width(inkex.Style(stroke_width=width), VIEWPORT)


RECT = inkex.Path("M 10 20 H 50 V 40 H 10 Z")  # the path of BBOX


@pytest.mark.parametrize(
    ("props", "pad"),
    [
        ({}, 8.0),  # miter joins with the miter limit 4
        ({"stroke_linejoin": "round"}, 2.0),
        ({"stroke_miterlimit": "10"}, 20.0),
    ],
)
def test_stroke_region_pads_corners(props, pad):
    region = ft.stroke_region(RECT, inkex.Style(stroke="#000", stroke_width="4", **props), VIEWPORT)
    assert (region.left, region.top) == pytest.approx((10 - pad, 20 - pad))
    assert (region.right, region.bottom) == pytest.approx((50 + pad, 40 + pad))


def test_stroke_region_of_butt_line_is_exact():
    line = inkex.Path("M 10 30 H 50")
    region = ft.stroke_region(line, inkex.Style(stroke="#000", stroke_width="4"), VIEWPORT)
    assert (region.left, region.top, region.right, region.bottom) == pytest.approx((10, 28, 50, 32))


def test_stroke_region_zero_width_paints_nothing():
    style = inkex.Style(stroke="#000", stroke_width="0")
    assert ft.stroke_region(RECT, style, VIEWPORT) is None


def hull_box(d, **props):
    points = ft.stroke_hull(inkex.Path(d), inkex.Style(**props), VIEWPORT)
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return pytest.approx((min(xs), min(ys), max(xs), max(ys)))


def test_stroke_hull_butt_line_is_exact():
    # The stroke of a tile stripe stays inside the tile, so it needs no tile clip.
    assert hull_box("M 0 4 H 8", stroke="#000", stroke_width="3") == (0, 2.5, 8, 5.5)


def test_stroke_hull_round_caps_go_past_the_ends():
    box = hull_box("M 0 4 H 8", stroke="#000", stroke_width="3", stroke_linecap="round")
    assert box == (-1.5, 2.5, 9.5, 5.5)


def test_stroke_hull_diagonal_line_is_exact():
    h = 1 / 2**0.5  # half the width 2, along the normal (-1, 1) / sqrt(2)
    assert hull_box("M 0 0 L 10 10", stroke="#000", stroke_width="2") == (-h, -h, 10 + h, 10 + h)


def test_stroke_hull_miter_join_uses_miter_limit():
    box = hull_box("M 0 0 L 10 0 L 10 10", stroke="#000", stroke_width="2")
    assert box == (-0.0, -4, 14, 10)


def test_stroke_hull_closed_path_has_join_at_start():
    box = hull_box("M 0 0 H 10 V 10 Z", stroke="#000", stroke_width="2", stroke_linejoin="round")
    assert box == (-1, -1, 11, 11)


def test_stroke_hull_curve_uses_control_points():
    box = hull_box("M 0 0 C 0 10 10 10 10 0", stroke="#000", stroke_width="2")
    assert box == (-1, -1, 11, 11)


def test_stroke_hull_dash_caps_can_be_anywhere():
    # Square caps on a curve: without dashes, only the two ends get caps.
    props = {"stroke": "#000", "stroke_width": "2", "stroke_linecap": "square"}
    plain = hull_box("M 0 0 C 0 10 10 10 10 0", **props)
    dashed = hull_box("M 0 0 C 0 10 10 10 10 0", stroke_dasharray="3 2", **props)
    r = 2**0.5
    assert plain == (-r, -r, 10 + r, 11)
    assert dashed == (-r, -r, 10 + r, 10 + r)


def test_stroke_hull_without_stroke_is_the_path():
    assert hull_box("M 0 0 H 10 V 5", stroke_width="8") == (0, 0, 10, 5)


@pytest.mark.parametrize(
    ("font", "width", "expected"),
    [("40px", "0.5em", 20.0), ("40px", "1ex", 20.0), ("150%", "1em", 16.0), (None, "1em", 16.0)],
)
def test_stroke_width_em_uses_font_size(font, width, expected):
    style = inkex.Style(stroke_width=width, **({"font_size": font} if font else {}))
    assert ft.stroke_width(style, VIEWPORT) == pytest.approx(expected)
