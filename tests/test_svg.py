import inkex
import pytest

from fp_svg import fmt_path, fmt_transform, opacity_value, set_props, take_props


def test_fmt_transform_keeps_small_values():
    assert fmt_transform(inkex.Transform()) is None
    # inkex treats this as the identity (tolerance 1e-5). The output must not.
    assert fmt_transform(inkex.Transform(translate=(4e-6, 0))) == "matrix(1 0 0 1 4e-06 0)"
    assert fmt_transform(inkex.Transform(translate=(0, -1962.581))) == "matrix(1 0 0 1 0 -1962.581)"


def test_fmt_path_keeps_digits():
    assert fmt_path("M 1.123456789 2 A 5 5 0 0 1 10 10 Z") == "M 1.123456789 2 A 5 5 0 0 1 10 10 Z"


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("0.25", 0.25),
        ("40%", 0.4),
        (" 1 ", 1.0),
        ("-0.5", 0.0),
        ("2", 1.0),
        ("150%", 1.0),
        ("-inf", 0.0),
        ("nan", 1.0),
        ("abc", 1.0),
    ],
)
def test_opacity_value_gives_range_0_to_1(text, value):
    assert opacity_value(text) == value


def doc_with(style_rule: str, rect_attrs: str):
    svg = inkex.load_svg(
        f'<svg xmlns="http://www.w3.org/2000/svg"><style>{style_rule}</style>'
        f'<rect id="r" width="5" height="5" {rect_attrs}/></svg>'.encode()
    ).getroot()
    return svg.getElementById("r")


def test_take_props_reads_style_attribute_and_presentation():
    rect = doc_with("", 'opacity="0.2" style="filter:url(#f);fill:red"')
    assert take_props(rect, {"opacity": "1", "filter": "none", "mask": "none"}) == {
        "opacity": "0.2",
        "filter": "url(#f)",
    }
    assert "opacity" not in rect.attrib
    assert dict(rect.style) == {"fill": "red"}


def test_take_props_stops_stylesheet_rule():
    rect = doc_with(".faded { opacity: 0.3 }", 'class="faded"')
    assert take_props(rect, {"opacity": "1"}) == {"opacity": "0.3"}
    # The rule stays in the document, so the target gets the initial value.
    assert rect.style.get("opacity") == "1"
    assert inkex.Style.cascaded_style(rect).get("opacity") == "1"


def test_set_props_accepts_text_and_removes_presentation():
    rect = doc_with("", 'fill="blue" style="stroke:red"')
    set_props(rect, {"fill": "none", "opacity": "0.4"})
    assert "fill" not in rect.attrib
    assert dict(rect.style) == {"stroke": "red", "fill": "none", "opacity": "0.4"}
