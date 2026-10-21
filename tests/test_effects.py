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
