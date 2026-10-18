import io
import os

import inkex
import pytest
from conftest import DATA, load

import flatternpattern as fp

SELECTION_SVG = os.path.join(DATA, "selection.svg")


@pytest.fixture
def svg():
    return inkex.load_svg(SELECTION_SVG).getroot()


def ids(items):
    return [i.elem.get_id() for i in items]


def select(svg, *names):
    return [svg.getElementById(n) for n in names]


def test_pattern_fill_is_target(svg):
    targets, skips = fp.collect(select(svg, "plain", "fallback"), True)
    assert ids(targets) == ["plain", "fallback"]
    assert all(t.prop == "fill" and t.pattern.get_id() == "pat" for t in targets)
    assert skips == []


def test_gradient_is_ignored(svg):
    assert fp.collect(select(svg, "gradient"), True) == ([], [])


def test_hatch_and_missing_are_skipped(svg):
    targets, skips = fp.collect(select(svg, "hatched", "missing"), True)
    assert targets == []
    assert ids(skips) == ["hatched", "missing"]
    assert "hatch" in skips[0].reason
    assert "not in the document" in skips[1].reason


def test_stroke_option(svg):
    targets, _ = fp.collect(select(svg, "stroked"), True)
    assert [(t.elem.get_id(), t.prop) for t in targets] == [("stroked", "stroke")]
    assert fp.collect(select(svg, "stroked"), False) == ([], [])


def test_group_recursion_uses_inherited_fill(svg):
    targets, skips = fp.collect(select(svg, "group"), True)
    # The clipPath content does not render, so the walk skips it.
    assert ids(targets) == ["child1", "child2"]
    assert skips == []


def test_group_and_child_selected_gives_one_target(svg):
    targets, _ = fp.collect(select(svg, "group", "child1"), True)
    assert ids(targets) == ["child1", "child2"]


def test_text_and_clone_are_skipped(svg):
    targets, skips = fp.collect(select(svg, "label", "clone"), True)
    assert targets == []
    assert ids(skips) == ["label", "clone"]
    assert "Object to Path" in skips[0].reason
    assert "Unlink Clone" in skips[1].reason


def test_text_children_give_one_skip_for_the_text(svg):
    targets, skips = fp.collect(select(svg, "spanned", "flow"), True)
    assert targets == []
    assert ids(skips) == ["spanned", "flow"]


def test_selected_tspan_reports_its_text(svg):
    _, skips = fp.collect(select(svg, "span"), True)
    assert ids(skips) == ["spanned"]


def test_url_to_non_paint_server_is_skipped(svg):
    targets, skips = fp.collect(select(svg, "bad_ref"), True)
    assert targets == []
    assert "rect element, which is not a paint server" in skips[0].reason


def test_image_with_pattern_is_ignored(svg):
    assert fp.collect(select(svg, "picture"), True) == ([], [])


def test_root_selection_walks_document(svg):
    targets, _ = fp.collect([svg], False)
    # in_defs is inside <defs>, so it is not a target.
    assert ids(targets) == ["plain", "fallback", "child1", "child2"]


def test_report_does_not_add_ids(capsys):
    out = io.BytesIO()
    fp.FlatternPattern().run([SELECTION_SVG, "--id=root"], output=out)
    assert "Skipped a <text> element" in capsys.readouterr().err
    texts = load(out.getvalue()).xpath("//svg:text")
    assert [t.get("id") for t in texts] == ["label", "spanned", None]


def test_report_without_change_writes_nothing(capsys):
    out = io.BytesIO()
    fp.FlatternPattern().run([SELECTION_SVG, "--id=label"], output=out)
    assert "Skipped #label" in capsys.readouterr().err
    # inkex writes no output when the document does not change.
    assert out.getvalue() == b""


def test_run_without_selection_aborts(capsys):
    with pytest.raises(SystemExit):
        fp.FlatternPattern().run([SELECTION_SVG], output=io.BytesIO())
    assert "Select one or more objects" in capsys.readouterr().err


def test_run_flattens_and_reports_skips(capsys):
    out = io.BytesIO()
    fp.FlatternPattern().run([SELECTION_SVG, "--id=plain", "--id=label"], output=out)
    assert "Skipped #label" in capsys.readouterr().err
    doc = load(out.getvalue())
    assert doc.getElementById("plain-flat") is not None
    assert doc.getElementById("plain").style.get("fill") == "none"
