"""Tests for the seam overlap of clipped copies (the seam_bleed option)."""

import os
import shutil

import pytest
from conftest import load
from PIL import ImageChops
from test_flatten import REAL, flatten, render_inkscape, render_rsvg

# In DOC, 1 user unit is 1 px.
BLEED = ("--seam_bleed=true", "--bleed_px=0.01")
# Content: a filled square, a line with no area, and a stroked square that goes
# past the tile box, so the tile gets a clip.
DOC = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100">'
    '<defs><pattern id="p" patternUnits="userSpaceOnUse" width="10" height="10" '
    'patternTransform="scale(2)">'
    '<rect id="square" width="5" height="5" fill="#06c"/>'
    '<path id="line" d="M 0 7 H 10"/>'
    '<rect id="framed" x="8" y="8" width="4" height="1" fill="#c60" stroke="#000"/>'
    "</pattern></defs>"
    '<rect id="r" class="target" width="50" height="50" fill="url(#p)"/></svg>'
)


def first_tile(tmp_path, *args):
    path = tmp_path / "doc.svg"
    path.write_text(DOC)
    doc = load(flatten(str(path), *args))
    cell = doc.getElementById("r-fill")[0]
    return doc, cell, list(cell[0])


def stroke_of(elem) -> str:
    return elem.style.get("stroke") or "none"


def test_without_bleed_shapes_get_no_stroke(tmp_path):
    _, _, (square, line, _) = first_tile(tmp_path)
    assert stroke_of(square) == "none"
    assert stroke_of(line) == "none"


def test_bleed_strokes_filled_shapes_only(tmp_path):
    _, _, (square, line, framed) = first_tile(tmp_path, *BLEED)
    # patternTransform scale(2): 0.01 user units are 0.005 in pattern space.
    assert stroke_of(square) == "#06c"
    assert float(square.style.get("stroke-width")) == pytest.approx(0.01)
    assert square.style.get("paint-order") == "stroke"
    # A stroke on the line would make it visible.
    assert stroke_of(line) == "none"
    # A stroked shape keeps its own stroke. The larger tile clip gives its overlap.
    assert framed.style.get("stroke") == "#000"


def test_bleed_makes_tile_clip_larger(tmp_path):
    doc, cell, _ = first_tile(tmp_path, *BLEED)
    clip = doc.getElementById(cell.get("clip-path")[5:-1])
    assert clip[0].get("d") == "M -0.005 -0.005 H 10.005 V 10.005 H -0.005 Z"


def lighter_pixels(before, after) -> int:
    """Return the number of pixels that are lighter in after by more than 40 of 255."""
    diff = ImageChops.subtract(after.convert("L"), before.convert("L"))
    return sum(diff.point(lambda v: 255 if v > 40 else 0).histogram()[255:])


RENDERERS = [
    pytest.param(render_inkscape, marks=pytest.mark.inkscape, id="inkscape"),
    pytest.param(
        render_rsvg,
        marks=pytest.mark.skipif(shutil.which("rsvg-convert") is None, reason="no rsvg-convert"),
        id="rsvg",
    ),
]


@pytest.mark.parametrize("render", RENDERERS)
def test_bleed_closes_seams_of_stock_pattern(render, tmp_path):
    # The half leaves of this tile meet at the tile edges. At about 134 dpi each
    # seam shows as a light line without the bleed. The default bleed is 0.2 px.
    source = os.path.join(REAL, "simple_shape_elaborate_stock_pattern.svg")
    before = render(source, tmp_path / "before.png", 1.4)
    counts = []
    for args in ((), ("--seam_bleed=true",)):
        flat = tmp_path / f"flat{len(args)}.svg"
        flat.write_bytes(flatten(source, *args))
        counts.append(lighter_pixels(before, render(flat, tmp_path / f"after{len(args)}.png", 1.4)))
    without, with_bleed = counts
    assert without > 500
    assert with_bleed < without / 10
