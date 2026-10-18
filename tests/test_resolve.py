import os

import inkex
import pytest
from conftest import DATA

from fp_resolve import ResolveError, resolve


@pytest.fixture(scope="module")
def svg():
    return inkex.load_svg(os.path.join(DATA, "resolve.svg")).getroot()


def res(svg, pid):
    return resolve(svg.getElementById(pid))


def content_ids(rp):
    return [c.get("id") for c in rp.content]


def test_wrapper_inherits_from_base(svg):
    rp = res(svg, "wrap")
    assert rp.ids == ("wrap", "base")
    assert content_ids(rp) == ["line"]
    assert (rp.x, rp.y, rp.width, rp.height) == ("0", "0", "41", "71")
    assert rp.units == "userSpaceOnUse"
    assert rp.viewbox == (0.0, 0.0, 41.0, 71.0)
    assert rp.aspect == "none"
    assert rp.transform == inkex.Transform("matrix(1.849 0.393 -0.393 1.849 -12.49 -1962.581)")


def test_nearest_node_wins(svg):
    rp = res(svg, "wrap2")
    assert rp.ids == ("wrap2", "wrap", "base")
    assert (rp.x, rp.width, rp.height) == ("5", "20", "71")
    assert rp.transform == res(svg, "wrap").transform
    assert content_ids(rp) == ["line"]


def test_defaults(svg):
    rp = res(svg, "bare")
    assert (rp.x, rp.y, rp.width, rp.height) == ("0", "0", "0", "0")
    assert rp.units == "objectBoundingBox"
    assert rp.content_units == "userSpaceOnUse"
    assert rp.transform == inkex.Transform()
    assert rp.viewbox is None
    assert rp.aspect == "xMidYMid meet"


@pytest.mark.parametrize(
    ("pid", "ids"),
    [
        ("titled", ["line"]),  # a title is not content, so the base gives it
        ("own_content", ["dot"]),  # own content hides the inherited content
        ("ns_wrap", ["line"]),  # a sodipodi child is not content
    ],
)
def test_content_source(svg, pid, ids):
    assert content_ids(res(svg, pid)) == ids


def test_href_comes_before_xlink_href(svg):
    assert res(svg, "both").ids == ("both", "bare")


@pytest.mark.parametrize(
    ("pid", "width"), [("external", "7"), ("to_rect", "8"), ("to_missing", "9")]
)
def test_invalid_href_ends_chain(svg, pid, width):
    rp = res(svg, pid)
    assert rp.ids == (pid,)
    assert rp.width == width
    assert rp.content == []


@pytest.mark.parametrize("pid", ["cycle1", "self"])
def test_cycle_is_an_error(svg, pid):
    with pytest.raises(ResolveError, match="circular href chain"):
        res(svg, pid)


def test_bad_viewbox_is_ignored(svg):
    assert res(svg, "bad_viewbox").viewbox is None


def test_empty_values_count_as_not_set(svg):
    rp = res(svg, "empty_wrap")
    assert rp.width == "4"
    assert rp.transform == inkex.Transform("translate(5,5)")


def test_invalid_values_count_as_not_set(svg):
    rp = res(svg, "bad_wrap")
    base = res(svg, "base")
    assert rp.viewbox == base.viewbox == (0.0, 0.0, 41.0, 71.0)
    assert rp.units == "userSpaceOnUse"
    assert rp.content_units == "userSpaceOnUse"
    assert rp.transform == inkex.Transform()
    assert rp.aspect == "none"
    assert (rp.x, rp.width) == ("0", "41")


def test_values_lose_outer_spaces(svg):
    rp = res(svg, "spaced")
    assert (rp.units, rp.x) == ("userSpaceOnUse", "2.5e1%")


def test_cycle_message_names_pattern_without_id(svg):
    anon = svg.xpath("//svg:pattern[@class='anon']")[0]
    with pytest.raises(ResolveError, match=r"<pattern> > #loop_a > #loop_b > #loop_a"):
        resolve(anon)
