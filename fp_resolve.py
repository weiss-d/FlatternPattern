"""Pattern resolution: follow the href chain and find the effective attributes.

The rules come from SVG 1.1 section 13.3 and SVG 2 "Paint Servers".
"""

import re
from collections.abc import Callable
from typing import NamedTuple

import inkex

SVG_NS = "{" + inkex.NSS["svg"] + "}"
# These SVG children do not count as pattern content.
NOT_CONTENT = {"title", "desc", "metadata"}

LENGTH_RE = re.compile(
    r"(?P<number>[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?)(?P<unit>%|px|pt|pc|mm|cm|in|em|ex)?"
)
TRANSFORM_RE = re.compile(r"(\s*[a-zA-Z]+\s*\([^()]*\)\s*,?)+\s*")
ASPECT_RE = re.compile(r"(defer\s+)?(none|x(Min|Mid|Max)Y(Min|Mid|Max))(\s+(meet|slice))?")
UNITS = {"userSpaceOnUse", "objectBoundingBox"}


def parse_viewbox(value: str | None) -> tuple[float, float, float, float] | None:
    """Return the four viewBox numbers, or None if value is missing or not valid."""
    parts = re.split(r"[\s,]+", (value or "").strip())
    try:
        x, y, w, h = map(float, parts)
    except ValueError:
        return None
    return x, y, w, h


def is_transform(value: str) -> bool:
    # inkex.Transform gives the identity for most bad text, so check the syntax first.
    try:
        return TRANSFORM_RE.fullmatch(value) is not None and inkex.Transform(value) is not None
    except ValueError:
        return False


# The attributes that a pattern inherits through href: the default and the check
# for a valid value. The spec treats a value that is not valid as not set.
CHAIN_ATTRS: dict[str, tuple[str | None, Callable[[str], object]]] = {
    "x": ("0", LENGTH_RE.fullmatch),
    "y": ("0", LENGTH_RE.fullmatch),
    "width": ("0", LENGTH_RE.fullmatch),
    "height": ("0", LENGTH_RE.fullmatch),
    "patternUnits": ("objectBoundingBox", UNITS.__contains__),
    "patternContentUnits": ("userSpaceOnUse", UNITS.__contains__),
    "patternTransform": (None, is_transform),
    "viewBox": (None, lambda v: parse_viewbox(v) is not None),
    "preserveAspectRatio": ("xMidYMid meet", ASPECT_RE.fullmatch),
}


class SkipError(ValueError):
    """The extension cannot flatten the target. The message tells the user why.

    No code catches this error yet. The effect does not call the resolver or the tiling."""


class Length(NamedTuple):
    value: float
    unit: str  # "", "%" or a unit name such as "mm"


def parse_length(text: str) -> Length:
    """Parse a length that LENGTH_RE accepts."""
    match = LENGTH_RE.fullmatch(text)
    if match is None:
        raise ValueError(f"Not a length: {text!r}")
    return Length(float(match["number"]), match["unit"] or "")


class ResolvedPattern(NamedTuple):
    ids: tuple[str, ...]  # all ids in the chain. No code uses them yet.
    content: list  # child elements to copy, from the first node that has some
    x: Length
    y: Length
    width: Length
    height: Length
    units: str  # patternUnits
    content_units: str  # patternContentUnits
    transform: inkex.Transform  # patternTransform
    viewbox: tuple[float, float, float, float] | None
    aspect: str  # preserveAspectRatio


def href_target(node):
    """Return the pattern that node refers to with href, or None.

    href comes before xlink:href (SVG 2). An external URL, a missing element
    or an element that is not a pattern ends the chain, as if there is no href."""
    ref = (node.get("href") or node.get("xlink:href") or "").strip()
    if not ref.startswith("#"):
        return None
    target = node.root.getElementById(ref[1:])
    return target if isinstance(target, inkex.Pattern) else None


def chain(pattern) -> list:
    """Return pattern and the patterns it inherits from, in href order."""
    nodes = [pattern]
    while (nxt := href_target(nodes[-1])) is not None:
        if nxt in nodes:  # elements compare by identity
            names = " > ".join(
                f"#{i}" if (i := n.get("id")) else "<pattern>" for n in [*nodes, nxt]
            )
            raise SkipError(f"The pattern has a circular href chain: {names}.")
        nodes.append(nxt)
    return nodes


def content_of(node) -> list:
    """Return the SVG children of node that are pattern content.

    Children in other namespaces, such as sodipodi or inkscape, do not render."""
    return [
        c
        for c in node
        if isinstance(c.tag, str)
        and c.tag.startswith(SVG_NS)
        and c.tag[len(SVG_NS) :] not in NOT_CONTENT
    ]


def first_valid(nodes, attr: str) -> str | None:
    """Return the first valid value of attr in the chain, with no outer spaces."""
    default, check = CHAIN_ATTRS[attr]
    # attrib gives the raw text. inkex get() returns str(Transform) for
    # patternTransform, which keeps 6 significant digits: -1962.581 becomes -1962.58.
    values = (n.attrib.get(attr, "").strip() for n in nodes)
    return next((v for v in values if check(v)), default)


def resolve(pattern) -> ResolvedPattern:
    """Combine the attributes and content of the href chain of pattern.

    Each attribute comes from the first node in the chain with a valid value."""
    nodes = chain(pattern)
    # Only patternTransform and viewBox have no default, so the other values are str.
    # The lengths passed LENGTH_RE in first_valid(), so parse_length() accepts them.
    first: dict = {attr: first_valid(nodes, attr) for attr in CHAIN_ATTRS}
    return ResolvedPattern(
        ids=tuple(filter(None, (n.get("id") for n in nodes))),
        content=next((c for n in nodes if (c := content_of(n))), []),
        x=parse_length(first["x"]),
        y=parse_length(first["y"]),
        width=parse_length(first["width"]),
        height=parse_length(first["height"]),
        units=first["patternUnits"],
        content_units=first["patternContentUnits"],
        transform=inkex.Transform(first["patternTransform"]),
        viewbox=parse_viewbox(first["viewBox"]),
        aspect=first["preserveAspectRatio"],
    )
