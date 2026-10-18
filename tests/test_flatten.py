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
from fp_resolve import resolve
from fp_tiling import plan, tile_transform

FLATTEN = os.path.join(DATA, "flatten")
FIXTURES = sorted(f for f in os.listdir(FLATTEN) if f.endswith(".svg"))
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
}
# rsvg-convert renders these inputs against the SVG spec. Inkscape and Chromium follow it.
RSVG_DIFFERS = {
    "viewbox_slice.svg": "rsvg-convert draws nothing for this viewBox with slice",
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
        assert "url(" not in str(target.specified_style().get("fill"))
        assert target.getparent().get("id") == f"{target.get('id')}-flat"


def test_lines_tile_count_and_clip():
    doc = load(flatten("lines.svg"))
    cells = list(doc.getElementById("target-fill"))
    # x 10..190 and y 10..190 with a 41 x 71 tile from (0, 0): 5 columns, 3 rows.
    assert len(cells) == 15
    # Stroked content always gets the tile clip.
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


def render_inkscape(svg_path, png_path):
    options = [
        "--export-type=png",
        f"--export-filename={png_path}",
        "--export-dpi=96",
        "--export-area-page",
        "--export-background=white",
        "--export-background-opacity=1",
    ]
    return render([INKSCAPE, svg_path, *options], png_path)


def render_rsvg(svg_path, png_path):
    return render(["rsvg-convert", "--background-color=white", "-o", png_path, svg_path], png_path)


def different_part(a, b) -> float:
    """Return the part of the pixels where one channel differs by more than PIXEL_TOLERANCE."""
    channels = ImageChops.difference(a, b).split()
    masks = [c.point(lambda v: 255 if v > PIXEL_TOLERANCE else 0) for c in channels]
    mask = masks[0]
    for other in masks[1:]:
        mask = ImageChops.lighter(mask, other)
    return mask.histogram()[255] / (a.width * a.height)


def compare(name, tmp_path, render, *args):
    flat = tmp_path / name
    flat.write_bytes(flatten(name, *args))
    before = render(os.path.join(FLATTEN, name), tmp_path / "before.png")
    after = render(flat, tmp_path / "after.png")
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
