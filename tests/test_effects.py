"""Tests for the effects of targets (clip, mask, filter) and hidden originals."""

from conftest import load
from test_flatten import flatten

DOTS = (
    '<pattern id="p" patternUnits="userSpaceOnUse" width="10" height="10">'
    '<rect width="5" height="5" fill="#06c"/></pattern>'
)


def write(tmp_path, defs: str, body: str) -> str:
    path = tmp_path / "doc.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="100" height="100" viewBox="0 0 100 100"><defs>{DOTS}{defs}</defs>{body}</svg>'
    )
    return str(path)


def test_hidden_original_in_switch_comes_after_output(tmp_path):
    # A <switch> renders its first child. The output group must be that child.
    path = write(
        tmp_path,
        "",
        '<switch id="s"><rect id="r" class="target" width="50" height="50" '
        'fill="url(#p)"/></switch>',
    )
    switch = load(flatten(path, "--keep_original=true")).getElementById("s")
    assert [c.get("id") for c in switch] == ["r-flat", "r-original"]


def test_effect_copy_keeps_its_internal_references(tmp_path):
    mask = (
        '<mask id="m" maskContentUnits="objectBoundingBox">'
        '<linearGradient id="g"><stop offset="0" stop-color="#fff"/></linearGradient>'
        '<rect width="1" height="1" fill="url(#g)"/></mask>'
    )
    path = write(
        tmp_path,
        mask,
        '<rect id="r" class="target" width="50" height="50" fill="url(#p)" mask="url(#m)"/>',
    )
    doc = load(flatten(path))
    # The content of the copy is in a wrapper group with the box transform.
    gradient, rect = doc.getElementById("m-flat")[0]
    assert gradient.get("id") == "g-flat"
    assert rect.get("fill") == "url(#g-flat)"


def test_bounding_box_effect_on_box_with_no_area_hides_output(tmp_path):
    # A filter in objectBoundingBox units on a box with no area disables rendering.
    path = write(
        tmp_path,
        '<filter id="f"><feGaussianBlur stdDeviation="1"/></filter>',
        '<path id="l" class="target" d="M 10 50 H 90" '
        'style="stroke:url(#p);stroke-width:10;filter:url(#f)"/>',
    )
    group = load(flatten(path)).getElementById("l-flat")
    assert group.style.get("display") == "none"


def test_effect_copy_follows_href_chain(tmp_path):
    filters = (
        '<filter id="base" x="0" y="0" width="1" height="1">'
        '<feGaussianBlur stdDeviation="1"/></filter>'
        '<filter id="f" xlink:href="#base"/>'
    )
    path = write(
        tmp_path,
        filters,
        '<rect id="r" class="target" x="10" y="20" width="50" '
        'height="40" fill="url(#p)" filter="url(#f)"/>',
    )
    copy = load(flatten(path)).getElementById("f-flat")
    # The region comes from #base: the box itself, not the default -10% / 120%.
    assert [copy.get(n) for n in ("x", "y", "width", "height")] == ["10", "20", "50", "40"]
    assert copy.get("xlink:href") is None
    assert copy[0].TAG == "feGaussianBlur"


def test_note_for_filter_primitive_units(tmp_path, capsys):
    path = write(
        tmp_path,
        '<filter id="f" primitiveUnits="objectBoundingBox">'
        '<feGaussianBlur stdDeviation="0.05"/></filter>',
        '<rect id="r" class="target" width="50" height="50" fill="url(#p)" filter="url(#f)"/>',
    )
    flatten(path)
    assert "Note for #r: the filter uses primitiveUnits=objectBoundingBox" in (
        capsys.readouterr().err
    )
