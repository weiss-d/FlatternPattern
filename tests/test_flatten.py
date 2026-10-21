import io
import math
import os
import shutil
import subprocess

import inkex
import pytest
from conftest import DATA, INKSCAPE, load
from PIL import Image, ImageChops

import flatternpattern as fp
from fp_clipped import needs_tile_clip, transform_within
from fp_resolve import resolve
from fp_tiling import Tile, plan, tile_transform

FLATTEN = os.path.join(DATA, "flatten")
FIXTURES = sorted(f for f in os.listdir(FLATTEN) if f.endswith(".svg"))
# Files made by hand in the Inkscape GUI.
REAL = os.path.join(DATA, "real")
REAL_FIXTURES = sorted(f for f in os.listdir(REAL) if f.endswith(".svg"))
# The real files have thin lines in small tiles. At 96 dpi, Inkscape and rsvg
# draw the pattern tile as a soft image, but the flattened copies stay sharp.
# A larger render scale makes that difference small.
REAL_SCALE = 4
REAL_TILE_LIMIT = "--tile_limit=20000"
# A pixel differs when one channel differs by more than this (of 255).
PIXEL_TOLERANCE = 64
# The test fails when more than this part of the pixels differ.
MAX_DIFFERENT = 0.001
# The elements to flatten in a fixture: "target" is one of their classes.
TARGETS = "//*[contains(concat(' ', @class, ' '), ' target ')]"
# Inkscape 1.4 renders these inputs against the SVG spec. rsvg and Chromium follow it.
# The option "Match the Inkscape view" makes these match Inkscape.
OBB_FIXTURES = ("bbox_units.svg", "bbox_tile.svg")
INKSCAPE_DIFFERS = {
    **dict.fromkeys(
        OBB_FIXTURES, "Inkscape puts objectBoundingBox tiles at the origin, not at the box"
    ),
    "visibility.svg": "Inkscape paints the pattern fill of a target with visibility:hidden",
    "effects_obb.svg": "Inkscape ignores mask regions and draws this filter region otherwise",
}
# rsvg-convert renders these inputs against the SVG spec. Inkscape and Chromium follow it.
RSVG_DIFFERS = {
    "viewbox_slice.svg": "rsvg-convert draws nothing for this viewBox with slice",
    "percent.svg": "rsvg-convert reads a percentage in pattern content against the tile",
}


def known_differences(differs):
    """Mark each fixture in differs as an expected failure."""
    return [
        pytest.param(n, marks=pytest.mark.xfail(reason=differs[n], strict=True))
        if n in differs
        else n
        for n in FIXTURES
    ]


def flatten(name, *args):
    """Run the extension on all elements with class="target". Return the output bytes."""
    path = os.path.join(FLATTEN, name)
    doc = inkex.load_svg(path).getroot()
    ids = [f"--id={e.get('id')}" for e in doc.xpath(TARGETS)]
    out = io.BytesIO()
    fp.FlatternPattern().run([path, *ids, *args], output=out)
    return out.getvalue()


@pytest.mark.parametrize("name", FIXTURES)
def test_no_pattern_paint_remains_on_targets(name):
    doc = load(flatten(name))
    for target in doc.xpath(TARGETS):
        style = target.specified_style()
        assert "url(" not in str(style.get("fill"))
        assert "url(" not in str(style.get("stroke"))
        assert target.getparent().get("id") == f"{target.get('id')}-flat"


def test_lines_tile_count_and_clip():
    doc = load(flatten("lines.svg"))
    cells = list(doc.getElementById("target-fill"))
    # x 10..190 and y 10..190 with a 41 x 71 tile from (0, 0): 5 columns, 3 rows.
    assert len(cells) == 15
    # The round caps of the lines go past the tile edge, so each cell gets the tile clip.
    assert all(c.get("clip-path") == "url(#target-tile)" for c in cells)
    assert doc.getElementById("target-shape") is not None


def test_filled_content_inside_tile_has_no_tile_clip():
    doc = load(flatten("filled_shapes.svg"))
    assert doc.getElementById("target-tile") is None
    assert all(c.get("clip-path") is None for c in doc.getElementById("target-fill"))


def test_overflowing_content_gets_tile_clip():
    doc = load(flatten("overflow.svg"))
    assert doc.getElementById("target-tile") is not None


def test_transforms_keep_full_precision():
    source = inkex.load_svg(os.path.join(FLATTEN, "rotated.svg")).getroot()
    target = source.getElementById("target")
    rp = resolve(source.getElementById("lines"))
    bbox = target.path.bounding_box()
    tile_plan = plan(rp, bbox, bbox, (200, 200), limit=2000)
    expected = [tile_transform(tile_plan, i, j).to_hexad() for i, j in tile_plan.cells]

    doc = load(flatten("rotated.svg"))
    # attrib gives the raw text. get() would give str(Transform) with 6 digits.
    written = [
        inkex.Transform(c.attrib["transform"]).to_hexad() for c in doc.getElementById("target-fill")
    ]
    assert len(written) == len(expected)
    for got, want in zip(written, expected, strict=True):
        assert all(math.isclose(g, w, abs_tol=1e-9) for g, w in zip(got, want, strict=True))


def test_content_does_not_inherit_from_target():
    doc = load(flatten("style_leak.svg"))
    fill = doc.getElementById("target-fill")
    triangle, line = fill[0][0][0], fill[0][0][1]
    assert triangle.specified_style().get("stroke") == "none"
    assert triangle.specified_style().get("fill") == "black"
    assert line.specified_style().get("stroke") == "#0a0"


def test_group_props_and_paint_order():
    doc = load(flatten("opacity_order.svg"))
    assert doc.getElementById("faded-fill").style.get("opacity") == "0.5"
    assert doc.getElementById("half-flat").style.get("opacity") == "0.4"
    assert doc.getElementById("half").style.get("opacity") is None
    assert doc.getElementById("under-flat")[0].get("id") == "under"  # stroke below fill
    assert doc.getElementById("over-flat")[-1].get("id") == "over"


def test_target_transform_moves_to_group():
    doc = load(flatten("transformed_group.svg"))
    assert doc.getElementById("target-flat").attrib["transform"] == "skewX(10)"
    assert "transform" not in doc.getElementById("target").attrib


def test_broken_pattern_is_resolved_once(monkeypatch):
    svg = inkex.load_svg(
        b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
        b'<defs><pattern id="a" xlink:href="#b"/><pattern id="b" xlink:href="#a"/></defs>'
        b'<rect id="r1" width="5" height="5" fill="url(#a)"/>'
        b'<rect id="r2" width="5" height="5" fill="url(#a)"/></svg>'
    ).getroot()
    calls = []
    monkeypatch.setattr(fp, "resolve", lambda p: calls.append(p) or resolve(p))
    targets, _ = fp.collect([svg.getElementById("r1"), svg.getElementById("r2")], False)
    _, skips, _ = fp.prepare(targets, (100, 100), limit=100)
    assert len(calls) == 1
    assert [s.elem.get("id") for s in skips] == ["r1", "r2"]
    assert all("circular" in s.reason for s in skips)


def test_tile_limit_stops_without_change(capsys):
    with pytest.raises(SystemExit):
        flatten("lines.svg", "--tile_limit=10")
    err = capsys.readouterr().err
    assert "The selection needs at least" in err
    assert "The limit is 10" in err


# Visual comparison


def render(command, png_path):
    subprocess.run(command, check=True, capture_output=True)
    return Image.open(png_path).convert("RGB")


def render_inkscape(svg_path, png_path, scale=1):
    options = [
        "--export-type=png",
        f"--export-filename={png_path}",
        f"--export-dpi={96 * scale}",
        "--export-area-page",
        "--export-background=white",
        "--export-background-opacity=1",
    ]
    return render([INKSCAPE, svg_path, *options], png_path)


def render_rsvg(svg_path, png_path, scale=1):
    command = ["rsvg-convert", f"--zoom={scale}", "--background-color=white", "-o", png_path]
    return render([*command, svg_path], png_path)


def different_part(a, b) -> float:
    """Return the part of the pixels where one channel differs by more than PIXEL_TOLERANCE."""
    channels = ImageChops.difference(a, b).split()
    masks = [c.point(lambda v: 255 if v > PIXEL_TOLERANCE else 0) for c in channels]
    mask = masks[0]
    for other in masks[1:]:
        mask = ImageChops.lighter(mask, other)
    return mask.histogram()[255] / (a.width * a.height)


def compare(name, tmp_path, render, *args, scale=1):
    """Flatten name and compare the renders before and after. name is a file in
    FLATTEN or an absolute path."""
    source = os.path.join(FLATTEN, name)
    # Not the name of the source: a generated source can be in tmp_path too.
    flat = tmp_path / f"flat-{os.path.basename(name)}"
    flat.write_bytes(flatten(name, *args))
    before = render(source, tmp_path / "before.png", scale)
    after = render(flat, tmp_path / "after.png", scale)
    assert before.size == after.size
    assert different_part(before, after) < MAX_DIFFERENT


@pytest.mark.inkscape
@pytest.mark.parametrize("name", known_differences(INKSCAPE_DIFFERS))
def test_inkscape_renders_output_like_input(name, tmp_path):
    compare(name, tmp_path, render_inkscape)


@pytest.mark.inkscape
@pytest.mark.parametrize("name", OBB_FIXTURES)
def test_inkscape_view_option_matches_inkscape(name, tmp_path):
    compare(name, tmp_path, render_inkscape, "--inkscape_obb=true")


def test_report_notes_inkscape_view_only_without_option(capsys):
    flatten("bbox_units.svg")
    assert "Inkscape 1.4 shows this pattern at another place" in capsys.readouterr().err
    flatten("bbox_units.svg", "--inkscape_obb=true")
    assert "another place" not in capsys.readouterr().err
    flatten("lines.svg")
    assert "another place" not in capsys.readouterr().err


@pytest.mark.skipif(shutil.which("rsvg-convert") is None, reason="rsvg-convert is not on PATH")
@pytest.mark.parametrize("name", known_differences(RSVG_DIFFERS))
def test_rsvg_renders_output_like_input(name, tmp_path):
    compare(name, tmp_path, render_rsvg)


@pytest.mark.inkscape
@pytest.mark.parametrize("name", REAL_FIXTURES)
def test_inkscape_renders_real_file_like_input(name, tmp_path):
    path = os.path.join(REAL, name)
    compare(path, tmp_path, render_inkscape, REAL_TILE_LIMIT, scale=REAL_SCALE)


@pytest.mark.skipif(shutil.which("rsvg-convert") is None, reason="rsvg-convert is not on PATH")
@pytest.mark.parametrize("name", REAL_FIXTURES)
def test_rsvg_renders_real_file_like_input(name, tmp_path):
    path = os.path.join(REAL, name)
    compare(path, tmp_path, render_rsvg, REAL_TILE_LIMIT, scale=REAL_SCALE)


# Pattern strokes


def test_fill_and_stroke_share_one_group():
    doc = load(flatten("stroke.svg"))
    assert [c.get("id") for c in doc.getElementById("both-flat")] == [
        "both-fill",
        "both-stroke",
        "both",
    ]
    stroke = doc.getElementById("both-stroke")
    assert stroke.style.get("opacity") == "0.6"
    assert stroke.style.get("mask") == "url(#both-stroke-mask)"
    outline = doc.getElementById("both-stroke-mask")[0]
    assert outline.style.get("stroke") == "#ffffff"
    assert outline.style.get("stroke-width") == "10"
    target = doc.getElementById("both").style
    assert (target.get("fill"), target.get("stroke")) == ("none", "none")


def test_stroke_layer_follows_paint_order():
    doc = load(flatten("stroke.svg"))
    # paint-order:stroke paints the stroke below the fill.
    flat = doc.getElementById("under-stroke-flat")
    assert [c.get("id") for c in flat] == ["under-stroke-stroke", "under-stroke"]
    # The normal order paints the stroke above the fill of the target.
    assert [c.get("id") for c in doc.getElementById("framed-flat")] == [
        "framed",
        "framed-stroke",
    ]


def test_stroke_of_line_is_flattened():
    # The box of a horizontal line has no area, but its stroke does.
    doc = load(flatten("stroke.svg"))
    assert len(doc.getElementById("line-stroke")) > 0


def test_tile_clip_only_when_content_leaves_tile():
    doc = load(flatten("seams.svg"))
    assert doc.getElementById("inside-tile-tile") is None
    assert doc.getElementById("across-tile-tile") is not None


def write_svg(tmp_path, body: str):
    path = tmp_path / "doc.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100">'
        '<defs><pattern id="p" patternUnits="userSpaceOnUse" width="10" height="10">'
        '<rect width="5" height="5"/></pattern></defs>' + body + "</svg>"
    )
    return str(path)


def test_stroke_with_zero_width_is_removed(tmp_path, capsys):
    path = write_svg(
        tmp_path,
        '<rect id="r" class="target" width="50" height="50" '
        'style="fill:none;stroke:url(#p);stroke-width:0"/>',
    )
    doc = load(flatten(path))
    assert doc.getElementById("r").style.get("stroke") == "none"
    assert "Removed the stroke of #r" in capsys.readouterr().err


def test_non_scaling_stroke_is_skipped(tmp_path, capsys):
    path = write_svg(
        tmp_path,
        '<rect id="r" class="target" width="50" height="50" '
        'style="fill:none;stroke:url(#p);stroke-width:4;vector-effect:non-scaling-stroke"/>',
    )
    flatten(path)
    assert "Skipped #r: The stroke does not scale" in capsys.readouterr().err


# A large stencil-like job, made at test time


def stencil(path, scale=0.4):
    """Write a 1000 x 1000 document with an outline that has a hole. Its fill is
    the asanoha-like tile of lines.svg, scaled by scale and rotated by 12 degrees."""
    outer, inner = [], []
    for k in range(180):
        angle = 2 * math.pi * k / 180
        r = 400 + 40 * math.sin(5 * angle) + 25 * math.cos(9 * angle)
        outer.append((500 + r * math.cos(angle), 500 + r * math.sin(angle)))
        inner.append((500 + 0.3 * r * math.cos(-angle), 480 + 0.3 * r * math.sin(-angle)))

    def ring(points):
        return "M " + " L ".join(f"{x:.3f} {y:.3f}" for x, y in points) + " Z"

    a = math.radians(12)
    c, s = scale * math.cos(a), scale * math.sin(a)
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="1000" '
        'viewBox="0 0 1000 1000"><defs>'
        '<pattern id="lines" patternUnits="userSpaceOnUse" width="41" height="71" '
        f'patternTransform="matrix({c} {s} {-s} {c} 0 0)">'
        '<g style="fill:none;stroke:#000;stroke-width:3;stroke-linecap:round">'
        '<path d="M 0 0 L 41 71 M 41 0 L 0 71"/><path d="M 20.5 0 V 71 M 0 35.5 H 41"/>'
        '<path d="M 0 0 L 20.5 35.5 L 41 0 M 0 71 L 20.5 35.5 L 41 71"/>'
        "</g></pattern></defs>"
        f'<path id="stencil" class="target" d="{ring(outer)} {ring(inner)}" '
        'style="fill:url(#lines);fill-rule:evenodd;stroke:#000;stroke-width:4"/></svg>'
    )
    return str(path)


def test_stencil_fits_default_limit(tmp_path):
    doc = load(flatten(stencil(tmp_path / "stencil.svg")))
    assert 1000 < len(doc.getElementById("stencil-fill")) <= 2000


def test_stencil_over_limit_stops(tmp_path, capsys):
    with pytest.raises(SystemExit):
        flatten(stencil(tmp_path / "stencil.svg"), "--tile_limit=1000")
    assert "The limit is 1000" in capsys.readouterr().err


@pytest.mark.inkscape
def test_inkscape_renders_stencil_like_input(tmp_path):
    # The lines are 1.2 user units wide, so the render needs the scale of the real files.
    compare(stencil(tmp_path / "stencil.svg"), tmp_path, render_inkscape, scale=REAL_SCALE)


def test_transform_within_composes_in_document_order():
    svg = inkex.load_svg(
        b'<svg xmlns="http://www.w3.org/2000/svg"><g id="top" transform="rotate(30)">'
        b'<g transform="translate(5 -3)"><g transform="scale(2 0.5)">'
        b'<path id="p" d="M 0 0 H 1" transform="skewX(15)"/></g></g></g></svg>'
    ).getroot()
    path = svg.getElementById("p")
    got = transform_within(path, svg.getElementById("top")).to_hexad()
    want = path.composed_transform().to_hexad()
    assert all(math.isclose(g, w, abs_tol=1e-9) for g, w in zip(got, want, strict=True))


def test_content_transform_keeps_full_precision(tmp_path):
    # The tile box starts at x = 0.123456789, so C = translate(0.123456789, 0).
    path = tmp_path / "doc.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100">'
        '<defs><pattern id="p" patternUnits="userSpaceOnUse" x="0.123456789" width="10" '
        'height="10"><path d="M 0 5 H 10" stroke="#000"/></pattern></defs>'
        '<rect id="r" class="target" width="50" height="50" fill="url(#p)"/></svg>'
    )
    doc = load(flatten(str(path)))
    content = doc.getElementById("r-fill")[0][0]
    assert content.attrib["transform"] == "matrix(1 0 0 1 0.123456789 0)"


@pytest.mark.parametrize(
    ("extra", "clip"), [("", False), (";vector-effect:non-scaling-stroke", True)]
)
def test_non_scaling_content_stroke_gets_tile_clip(extra, clip):
    template = inkex.Group()
    template.append(inkex.PathElement(d="M 0 4 H 8", style=f"stroke:#000;stroke-width:3{extra}"))
    assert needs_tile_clip(template, Tile(0, 0, 8, 8), (100, 100)) is clip


# Keep original


def test_keep_original_adds_hidden_copy_with_pattern():
    doc = load(flatten("transformed_group.svg", "--keep_original=true"))
    original = doc.getElementById("target-original")
    # The copy is the sibling before the output group and keeps its own transform.
    assert original.getnext() is doc.getElementById("target-flat")
    assert original.attrib["transform"] == "skewX(10)"
    assert original.style.get("display") == "none"
    assert "url(#" in str(original.style.get("fill") or original.get("fill"))
    ids = [e.get("id") for e in doc.iter() if isinstance(e.tag, str) and e.get("id")]
    assert len(ids) == len(set(ids))


def test_keep_original_of_target_that_paints_nothing(tmp_path):
    path = write_svg(
        tmp_path,
        '<rect id="r" class="target" width="50" height="50" '
        'style="fill:none;stroke:url(#p);stroke-width:0"/>',
    )
    doc = load(flatten(path, "--keep_original=true"))
    assert "url(#p)" in str(doc.getElementById("r-original").style.get("stroke"))
    assert doc.getElementById("r").style.get("stroke") == "none"


def test_no_original_without_option():
    assert load(flatten("lines.svg")).getElementById("target-original") is None


@pytest.mark.inkscape
def test_hidden_original_does_not_render(tmp_path):
    compare("stroke.svg", tmp_path, render_inkscape, "--keep_original=true")


# Effects in objectBoundingBox units on the output group


def test_effects_change_to_user_space():
    doc = load(flatten("effects_obb.svg"))
    group = doc.getElementById("regioned-flat")
    assert group.style.get("mask") == "url(#region-flat)"
    region = doc.getElementById("region-flat")
    # x = 10 + 0.25 * 80 and width = 0.5 * 80 in the box of the target.
    assert [region.get(n) for n in ("maskUnits", "x", "width")] == ["userSpaceOnUse", "30", "40"]
    blur = doc.getElementById("blur-flat")
    assert [blur.get(n) for n in ("filterUnits", "x", "width")] == ["userSpaceOnUse", "110", "80"]
    circle = doc.getElementById("round-flat")[0]
    assert circle.attrib["transform"] == "matrix(80 0 0 80 10 10)"
    assert doc.getElementById("round-flat").get("clipPathUnits") == "userSpaceOnUse"
    left = doc.getElementById("left-flat")
    assert left[0].attrib["transform"] == "matrix(80 0 0 80 110 10)"
    # The default region -10% / 120% of the box 110, 10, 80, 80.
    assert [left.get(n) for n in ("x", "y", "width", "height")] == ["102", "2", "96", "96"]
    assert all(e.get("id") is None for e in left.iterdescendants())


def test_no_effect_copy_without_target_effects():
    doc = load(flatten("css_hidden.svg"))
    # Only the effects of a target move to the output group. The targets in
    # css_hidden.svg have none (the filter there is on the tile content).
    assert not doc.xpath(
        "//*[substring(@id, string-length(@id) - 4) = '-flat' and "
        "(self::svg:mask or self::svg:filter or self::svg:clipPath)]"
    )
