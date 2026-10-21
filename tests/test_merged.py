"""Tests for merged mode. Most tests run Inkscape, because merged mode runs it."""

import contextlib
import functools
import io
import os
import shutil

import inkex
import pytest
from conftest import load
from inkex.command import CommandNotFound
from test_flatten import (
    FIXTURES,
    FLATTEN,
    INKSCAPE_DIFFERS,
    MAX_DIFFERENT,
    REAL,
    REAL_FIXTURES,
    REAL_SCALE,
    REAL_TILE_LIMIT,
    RSVG_DIFFERS,
    different_part,
    flatten,
    render_inkscape,
    render_rsvg,
    stencil,
)

import fp_merged
from flatternpattern import merge_notes, opacity_text

MERGED = "--mode=merged"
SCALE = 2
# Merged mode paints the tile with its first color only.
MORE_COLORS = "The tile content has more than one color"
MERGED_INKSCAPE_DIFFERS = {
    **INKSCAPE_DIFFERS,
    **dict.fromkeys(
        ["filled_shapes.svg", "style_leak.svg", "viewbox_slice.svg", "transforms_px_units.svg"],
        MORE_COLORS,
    ),
}
# rsvg draws nothing for viewbox_slice.svg (see RSVG_DIFFERS), before and after.
MERGED_RSVG_DIFFERS = {
    **dict.fromkeys(
        ["filled_shapes.svg", "style_leak.svg", "transforms_px_units.svg"], MORE_COLORS
    ),
    "percent.svg": RSVG_DIFFERS["percent.svg"],
}
SOURCES = [os.path.join(FLATTEN, n) for n in FIXTURES] + [
    os.path.join(REAL, n) for n in REAL_FIXTURES
]
needs_rsvg = pytest.mark.skipif(shutil.which("rsvg-convert") is None, reason="no rsvg-convert")


def sources(differs):
    """Return SOURCES with each file in differs marked as an expected failure."""
    params = []
    for path in SOURCES:
        name = os.path.basename(path)
        marks = [pytest.mark.xfail(reason=differs[name], strict=True)] if name in differs else []
        params.append(pytest.param(path, id=name, marks=marks))
    return params


@functools.cache
def merged(name: str) -> tuple[bytes, str]:
    """Flatten name (see flatten()) in merged mode once for all tests. Return the
    output and the messages."""
    messages = io.StringIO()
    with contextlib.redirect_stderr(messages):
        out = flatten(name, MERGED, REAL_TILE_LIMIT)
    return out, messages.getvalue()


def compare_merged(path, tmp_path, render):
    flat = tmp_path / f"flat-{os.path.basename(path)}"
    flat.write_bytes(merged(path)[0])
    scale = REAL_SCALE if path.startswith(REAL) else SCALE
    before = render(path, tmp_path / "before.png", scale)
    after = render(flat, tmp_path / "after.png", scale)
    assert different_part(before, after) < MAX_DIFFERENT


@pytest.mark.inkscape
@pytest.mark.parametrize("path", sources(MERGED_INKSCAPE_DIFFERS))
def test_inkscape_renders_merged_like_input(path, tmp_path):
    compare_merged(path, tmp_path, render_inkscape)


@pytest.mark.inkscape
@needs_rsvg
@pytest.mark.parametrize("path", sources(MERGED_RSVG_DIFFERS))
def test_rsvg_renders_merged_like_input(path, tmp_path):
    compare_merged(path, tmp_path, render_rsvg)


@pytest.mark.inkscape
def test_merged_fill_is_one_path():
    doc = load(merged(os.path.join(FLATTEN, "lines.svg"))[0])
    fill = doc.getElementById("target-fill")
    assert [c.TAG for c in fill] == ["path"]
    assert fill[0].style.get("fill") == "#000"
    assert doc.getElementById("target-tile") is None  # no clipped copies


@pytest.mark.inkscape
def test_stencil_merged_is_one_path(tmp_path):
    doc = load(flatten(stencil(tmp_path / "stencil.svg"), MERGED))
    assert [c.TAG for c in doc.getElementById("stencil-fill")] == ["path"]


@pytest.mark.inkscape
def test_merged_notes():
    assert (
        "Made clipped copies for the fill of #blurred: The tile content has a filter"
        in (merged(os.path.join(FLATTEN, "css_hidden.svg"))[1])
    )
    assert (
        "#target has more than one color. The merged path uses the first color, #06c."
        in (merged(os.path.join(FLATTEN, "filled_shapes.svg"))[1])
    )
    assert (
        "Made clipped copies for the stroke of #framed"
        in (merged(os.path.join(FLATTEN, "stroke.svg"))[1])
    )
    # The opacity of the target itself stays on the output group, so it gives no note.
    assert "lowest opacity" not in merged(os.path.join(FLATTEN, "opacity_order.svg"))[1]


@pytest.mark.inkscape
def test_merged_notes_opacity(tmp_path, capsys):
    path = tmp_path / "faded.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100">'
        '<defs><pattern id="p" patternUnits="userSpaceOnUse" width="10" height="10">'
        '<rect width="5" height="5" fill="#06c" fill-opacity="0.5"/></pattern></defs>'
        '<rect id="r" class="target" width="50" height="50" fill="url(#p)"/></svg>'
    )
    flatten(str(path), MERGED)
    assert (
        "The lowest opacity in the tile content of #r is 0.5. The merged path is opaque."
        in capsys.readouterr().err
    )


def test_merged_without_inkscape_makes_clipped_copies(monkeypatch, capsys):
    def missing(*args, **kwargs):
        raise CommandNotFound("inkscape")

    monkeypatch.setattr(fp_merged, "inkscape_command", missing)
    doc = load(flatten("lines.svg", MERGED))
    assert len(doc.getElementById("target-fill")) == 15  # one clipped copy for each tile
    assert "Made clipped copies for the fill of #target: Inkscape did not run" in (
        capsys.readouterr().err
    )


def test_merged_with_skipped_actions_makes_clipped_copies(monkeypatch, capsys):
    # Inkscape stops an action list at an error. Then the document comes back as it was.
    monkeypatch.setattr(fp_merged, "inkscape_command", lambda svg, **kwargs: svg)
    doc = load(flatten("lines.svg", MERGED))
    assert len(doc.getElementById("target-fill")) == 15
    assert "Inkscape did not complete the boolean operations" in capsys.readouterr().err


def many_targets(path, count=80):
    """Write a document with count small rects that share one pattern."""
    rects = "".join(
        f'<rect id="r{k}" class="target" x="{k % 10 * 20}" y="{k // 10 * 20}" '
        'width="15" height="15" fill="url(#dots)"/>'
        for k in range(count)
    )
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="160" viewBox="0 0 200 160">'
        '<defs><pattern id="dots" patternUnits="userSpaceOnUse" width="5" height="5">'
        '<circle cx="2.5" cy="2.5" r="1.5" fill="#06c"/></pattern></defs>' + rects + "</svg>"
    )
    return str(path)


@pytest.mark.inkscape
def test_many_targets_use_one_run_for_each_step(tmp_path, monkeypatch, capsys):
    # The actions of 80 targets are longer than the Windows command line limit.
    calls = []
    real = fp_merged.inkscape_command
    monkeypatch.setattr(
        fp_merged, "inkscape_command", lambda svg, **kw: calls.append(kw) or real(svg, **kw)
    )
    doc = load(flatten(many_targets(tmp_path / "many.svg"), MERGED))
    assert "Made clipped copies" not in capsys.readouterr().err
    assert all([c.TAG for c in doc.getElementById(f"r{k}-fill")] == ["path"] for k in range(80))
    assert len(calls) == 2  # one run for the tile, one for all targets


def test_line_fill_does_not_count_as_color():
    line = inkex.PathElement(d="M 0 4 H 8", style="stroke:#c00")
    assert fp_merged.paints(line, line) == {"stroke": "#c00"}


@pytest.mark.parametrize(
    ("style", "expected"),
    [
        ("fill:#000;fill-opacity:0", {}),  # an invisible background
        ("fill:#000;opacity:0;stroke:#c00", {}),
        ("fill:#000;stroke:#c00;stroke-opacity:0%", {"fill": "#000"}),
    ],
)
def test_paint_with_opacity_0_paints_nothing(style, expected):
    rect = inkex.Rectangle(width="5", height="5", style=style)
    assert fp_merged.paints(rect, rect) == expected


def test_shown_opacity():
    group = inkex.Group(style="opacity:0.5")
    rect = inkex.Rectangle(width="1", height="1", style="fill-opacity:0.5;stroke-opacity:2")
    group.append(rect)
    assert fp_merged.shown_opacity(rect, group, {"fill": "#000", "stroke": "#000"}) == 0.25
    assert fp_merged.shown_opacity(rect, rect, {"fill": "#000"}) == 0.5
    # The fill-opacity does not matter when only the stroke paints. A renderer
    # clamps the stroke-opacity to 1.
    assert fp_merged.shown_opacity(rect, rect, {"stroke": "#000"}) == 1


@pytest.mark.parametrize(("opacity", "noted"), [(0.5, True), (0.989, True), (0.994819, False)])
def test_opacity_note_skips_values_near_one(opacity, noted):
    rect = inkex.Rectangle(id="r")
    notes = merge_notes(rect, fp_merged.Content([], ["#000"], opacity))
    assert any("lowest opacity" in n for n in notes) == noted


@pytest.mark.parametrize(
    ("opacity", "text"),
    [(0.5, "0.5"), (0.29, "0.29"), (0.98999, "0.9899"), (0.00001, "0.0001"), (0.125, "0.125")],
)
def test_opacity_text_rounds_down_with_4_decimals(opacity, text):
    assert opacity_text(opacity) == text


def test_merged_fill_that_paints_nothing_is_removed(tmp_path, capsys):
    path = tmp_path / "unpainted.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100">'
        '<defs><pattern id="p" patternUnits="userSpaceOnUse" width="10" height="10">'
        '<rect width="5" height="5" fill="none"/></pattern></defs>'
        '<rect id="r" class="target" width="50" height="50" fill="url(#p)"/></svg>'
    )
    doc = load(flatten(str(path), MERGED))
    assert doc.getElementById("r").style.get("fill") == "none"
    assert doc.getElementById("r-fill") is None
    assert "Removed the fill of #r: the pattern paints nothing." in capsys.readouterr().err


def document(path, content: str, body: str, style: str = "") -> str:
    """Write a 100 x 100 document with the pattern #p and return its path."""
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100">'
        f'<style>{style}</style><defs><pattern id="p" patternUnits="userSpaceOnUse" '
        f'width="10" height="10">{content}</pattern></defs>{body}</svg>'
    )
    return str(path)


DOT = '<circle cx="5" cy="5" r="3" fill="#06c"/>'


@pytest.mark.inkscape
def test_merged_ignores_content_that_does_not_render(tmp_path):
    hidden = '<defs><rect id="big" width="10" height="10"/></defs>'
    path = document(
        tmp_path / "d.svg",
        hidden + DOT,
        '<rect id="r" class="target" width="50" height="50" fill="url(#p)"/>',
    )
    box = inkex.Path(load(flatten(path, MERGED)).getElementById("r-fill")[0].get("d"))
    # Only the 25 dots, one subpath each. The rect in <defs> would fill the whole target.
    assert len(box.to_superpath()) == 25


@pytest.mark.inkscape
def test_temporary_document_has_the_frame_of_the_source(monkeypatch):
    # A percentage in a copied shape then refers to the same viewport as in the source.
    documents = []
    monkeypatch.setattr(
        fp_merged, "inkscape_command", lambda svg, **kw: documents.append(svg) or svg
    )
    flatten("lines.svg", MERGED)
    svg = inkex.load_svg(documents[0]).getroot()
    assert (svg.get("width"), svg.get("height"), svg.get("viewBox")) == (
        "200",
        "200",
        "0 0 200 200",
    )


@pytest.mark.inkscape
def test_merged_path_resists_stylesheet(tmp_path):
    path = document(
        tmp_path / "s.svg",
        DOT,
        '<rect id="r" class="target" width="50" height="50" fill="url(#p)"/>',
        style="path { opacity: 0.3 }",
    )
    merged_path = load(flatten(path, MERGED)).getElementById("r-fill")[0]
    assert merged_path.style.get("opacity") == "1"


def test_merged_with_bad_output_makes_clipped_copies(monkeypatch, capsys):
    monkeypatch.setattr(fp_merged, "inkscape_command", lambda svg, **kwargs: b"<svg")
    doc = load(flatten("lines.svg", MERGED))
    assert len(doc.getElementById("target-fill")) == 15
    assert "Made clipped copies for the fill of #target: Inkscape did not" in (
        capsys.readouterr().err
    )


def test_one_note_for_one_error(tmp_path, monkeypatch, capsys):
    def missing(*args, **kwargs):
        raise CommandNotFound("inkscape")

    monkeypatch.setattr(fp_merged, "inkscape_command", missing)
    flatten(many_targets(tmp_path / "many.svg", count=3), MERGED)
    err = capsys.readouterr().err
    assert err.count("Inkscape did not run") == 1
    assert "Made clipped copies for the fill of #r0, #r1, #r2: Inkscape did not run" in err
