import inkex
from conftest import load
from test_flatten import flatten

from fp_cleanup import references


def patterns(doc) -> set[str]:
    return {p.get("id") for p in doc.xpath("//svg:pattern")}


def test_chain_is_removed():
    # The cleanup removes the wrapper first. Then nothing refers to the base either.
    assert patterns(load(flatten("chained.svg"))) == set()


def test_option_off_keeps_patterns():
    doc = load(flatten("chained.svg", "--cleanup_defs=false"))
    assert patterns(doc) == {"base", "wrap"}


def test_hidden_original_keeps_its_patterns():
    doc = load(flatten("chained.svg", "--keep_original=true"))
    assert patterns(doc) == {"base", "wrap"}


def write(tmp_path, body: str) -> str:
    path = tmp_path / "doc.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        'width="100" height="100" viewBox="0 0 100 100">' + body + "</svg>"
    )
    return str(path)


def test_pattern_of_other_element_stays(tmp_path):
    path = write(
        tmp_path,
        '<defs><pattern id="p" patternUnits="userSpaceOnUse" width="10" height="10">'
        '<rect width="5" height="5"/></pattern></defs>'
        '<rect id="a" class="target" width="40" height="40" fill="url(#p)"/>'
        '<rect id="b" x="50" width="40" height="40" fill="url(#p)"/>',
    )
    assert patterns(load(flatten(path))) == {"p"}


def test_pattern_with_used_content_stays(tmp_path):
    # The tile copies hold a <use> that refers to #dot in the pattern content.
    path = write(
        tmp_path,
        '<defs><pattern id="p" patternUnits="userSpaceOnUse" width="10" height="10">'
        '<rect id="dot" width="3" height="3"/><use xlink:href="#dot" x="5" y="5"/>'
        "</pattern></defs>"
        '<rect id="a" class="target" width="40" height="40" fill="url(#p)"/>',
    )
    assert patterns(load(flatten(path))) == {"p"}


def test_references():
    svg = inkex.load_svg(
        b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
        b"<style>.a { fill: url('#css') }</style>"
        b'<rect fill="url(#attr)" style="stroke:url( #style )"/>'
        b'<use xlink:href="#xlink"/><use href="#plain"/><a href="https://x.org/#no"/></svg>'
    ).getroot()
    assert set(references(svg)) == {"css", "attr", "style", "xlink", "plain"}


def test_reference_inside_the_pattern_does_not_keep_it(tmp_path):
    # The stroke paints nothing, so no tile copy refers to #dot. Only the
    # pattern refers to its own content.
    path = write(
        tmp_path,
        '<defs><pattern id="p" patternUnits="userSpaceOnUse" width="10" height="10">'
        '<rect id="dot" width="3" height="3"/><use xlink:href="#dot" x="5" y="5"/>'
        "</pattern></defs>"
        '<rect id="a" class="target" width="40" height="40" '
        'style="fill:none;stroke:url(#p);stroke-width:0"/>',
    )
    assert patterns(load(flatten(path))) == set()
