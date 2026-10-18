"""Shared helpers to write SVG output with full precision.

inkex writes transforms and path data with 6 significant digits. Near a
coordinate of 2000 that gives an error of about 0.01. All output code in this
extension writes numbers through these helpers instead.
"""

import inkex

# Inheritable painting properties and their initial values. Tile content copies
# move to a new parent, so a group with these values stops inheritance from the
# target and its ancestors.
RESET_STYLE = {
    "fill": "black",
    "fill-opacity": "1",
    "fill-rule": "nonzero",
    "stroke": "none",
    "stroke-width": "1",
    "stroke-opacity": "1",
    "stroke-linecap": "butt",
    "stroke-linejoin": "miter",
    "stroke-miterlimit": "4",
    "stroke-dasharray": "none",
    "stroke-dashoffset": "0",
    "marker-start": "none",
    "marker-mid": "none",
    "marker-end": "none",
    "paint-order": "normal",
    "clip-rule": "nonzero",
    "color": "black",
    "visibility": "visible",
}


def num(value: float) -> str:
    text = f"{value:.12g}"
    return "0" if text == "-0" else text


def fmt_transform(transform: inkex.Transform) -> str | None:
    """Return the transform as a full-precision matrix, or None for the identity."""
    # Not "if not transform": inkex compares with the identity at a tolerance of 1e-5.
    if tuple(transform.to_hexad()) == (1, 0, 0, 1, 0, 0):
        return None
    return "matrix(" + " ".join(num(v) for v in transform.to_hexad()) + ")"


def fmt_path(path) -> str:
    """Return the path data with full precision."""
    return " ".join(" ".join([cmd.letter, *(num(a) for a in cmd.args)]) for cmd in inkex.Path(path))


def set_raw(elem, name: str, value: str | None):
    """Set or remove an attribute without the inkex conversion (which rounds)."""
    if value is None:
        elem.attrib.pop(name, None)
    else:
        elem.attrib[name] = value


def take_props(elem, initials: dict) -> dict:
    """Remove the properties in initials from elem and return their values as text.

    The values come from the full cascade: stylesheet rules, the style attribute
    and presentation attributes. A stylesheet rule stays in the document, so
    elem gets the initial value in its style attribute to stop the rule."""
    cascaded = inkex.Style.cascaded_style(elem)
    taken = {name: cascaded.get(name) for name in initials if name in cascaded}
    # inkex Style.pop() does not return the value, so this uses del.
    style = elem.style
    for name in taken:
        elem.attrib.pop(name, None)
        if name in style:
            del style[name]
    elem.style = style
    still = inkex.Style.cascaded_style(elem)
    set_props(elem, {name: initials[name] for name in taken if name in still})
    return taken


def set_props(elem, values: dict):
    """Set the properties in values (as text) in the style attribute of elem.

    inkex Style item assignment rejects some text values, for example an opacity,
    so this makes a new Style from text."""
    if not values:
        return
    style = elem.style
    elem.style = inkex.Style({**{k: style.get(k) for k in style}, **values})
    for name in values:
        elem.attrib.pop(name, None)  # a presentation attribute must not stay


def unique_id(svg, wanted: str) -> str:
    """Return wanted if no element uses it, else a free id with wanted as prefix."""
    return wanted if svg.getElementById(wanted) is None else svg.get_unique_id(wanted + "-")
