#!/usr/bin/env python
"""FlatternPattern: replace pattern fills with real, tiled geometry."""

from typing import NamedTuple

import inkex
from inkex.styles import Style

# Containers whose content does not render directly, so the walk skips them.
NON_RENDERED = {
    "defs",
    "clipPath",
    "mask",
    "marker",
    "pattern",
    "symbol",
    "metadata",
    "title",
    "desc",
    "style",
    "script",
}
TEXT_ROOTS = (inkex.TextElement, inkex.FlowRoot)


class Target(NamedTuple):
    elem: inkex.BaseElement
    prop: str  # "fill" or "stroke"
    pattern: inkex.Pattern


class Skip(NamedTuple):
    elem: inkex.BaseElement
    reason: str


def walk(elem, parent_style: Style):
    """Yield (element, specified style) for elem and its rendered descendants.

    Each style comes from the parent style, so every element gets one cascade."""
    if not isinstance(elem.tag, str) or elem.TAG in NON_RENDERED:
        return  # comments, processing instructions, non-rendered containers
    style = Style.add_inherited(Style.cascaded_style(elem), parent_style)
    style.element = elem
    yield elem, style
    for child in elem:
        yield from walk(child, style)


def paint(style: Style, prop: str):
    """Return the parsed paint of prop: a paint server element, a color, None,
    or the ValueError that inkex raises for a url() to a missing element.

    The url() lookup uses style.element, which walk() sets."""
    try:
        return style(prop)
    except ValueError as err:
        return err


def text_root(elem):
    """Return the text or flowRoot element that holds elem (or elem itself), else None.

    Text children such as tspan, and the shapes in a flowRegion, belong to their text."""
    return next((a for a in (elem, *elem.ancestors()) if isinstance(a, TEXT_ROOTS)), None)


def classify(elem, prop: str, style: Style) -> Target | Skip | None:
    """Make a Target for a pattern paint, a Skip for an unsupported one,
    or None when there is nothing to flatten."""
    match paint(style, prop), elem:
        case ValueError(), _:
            return Skip(elem, f"The {prop} refers to an element that is not in the document.")
        case inkex.BaseElement(TAG="hatch"), _:
            return Skip(elem, f"The {prop} is a hatch. The extension does not support hatches.")
        case inkex.MeshGradient(), _:
            return Skip(
                elem,
                f"The {prop} is a mesh gradient. The extension does not support mesh gradients.",
            )
        case inkex.Pattern(), _ if (text := text_root(elem)) is not None:
            return Skip(
                text,
                "This is a text object. Use Path > Object to Path, then run the extension again.",
            )
        case inkex.Pattern(), inkex.Use():
            return Skip(
                elem,
                "This is a clone. Use Edit > Clone > Unlink Clone, then run the extension again.",
            )
        case inkex.Pattern() as server, (
            inkex.PathElement()
            | inkex.Rectangle()
            | inkex.Circle()
            | inkex.Ellipse()
            | inkex.Line()
            | inkex.Polyline()
        ):
            return Target(elem, prop, server)
        case inkex.Pattern() | inkex.Gradient(), _:
            # Gradients need no flattening. A group gives the pattern to its
            # children, and an image or other element does not use the paint.
            return None
        case inkex.BaseElement() as server, _ if server.TAG.lower() != "solidcolor":
            return Skip(
                elem, f"The {prop} refers to a {server.TAG} element, which is not a paint server."
            )
        case _:
            # Colors, none, context-fill and solid colors need no flattening.
            return None


def collect(selection, stroke_patterns: bool):
    """Find the pattern-painted shapes in the selection and its groups."""
    chosen = set(selection)
    # A selected element inside a selected group comes from the walk of the group.
    tops = [e for e in selection if not any(a in chosen for a in e.ancestors())]
    props = ("fill", "stroke") if stroke_patterns else ("fill",)
    results = [
        classify(elem, prop, style)
        for top in tops
        for elem, style in walk(top, parent_style(top))
        for prop in props
    ]
    # The children of one text give the same Skip, so keep each one only once.
    return (
        [r for r in results if isinstance(r, Target)],
        list(dict.fromkeys(r for r in results if isinstance(r, Skip))),
    )


def parent_style(elem) -> Style:
    """Return the specified style of the parent, or an empty style for the root."""
    parent = elem.getparent()
    return Style() if parent is None else parent.specified_style()


def label(elem) -> str:
    """Name elem in a message by its id, or by its tag when it has no id."""
    eid = elem.get("id")
    return f"#{eid}" if eid else f"a <{elem.TAG}> element"


class FlatternPattern(inkex.EffectExtension):
    def add_arguments(self, pars):
        pars.add_argument("--tab", default="options")
        pars.add_argument("--mode", default="clipped", choices=("clipped", "merged"))
        pars.add_argument("--stroke_patterns", type=inkex.Boolean, default=True)
        pars.add_argument("--keep_original", type=inkex.Boolean, default=False)
        pars.add_argument("--cleanup_defs", type=inkex.Boolean, default=True)
        pars.add_argument("--tile_limit", type=int, default=2000)
        pars.add_argument("--tolerance", type=float, default=0.01)

    def effect(self):
        if not self.svg.selection:
            raise inkex.AbortExtension(
                "Select one or more objects with a pattern fill, then run the extension again."
            )
        targets, skips = collect(list(self.svg.selection.values()), self.options.stroke_patterns)
        # Only the selection walk is wired up. The flattening of the targets
        # does not exist yet, so the document does not change.
        lines = [f"Found {len(targets)} pattern paint(s) to flatten."]
        # get_id() writes a new id onto an element without one, so use get("id").
        lines += [f"Skipped {label(s.elem)}: {s.reason}" for s in skips]
        self.msg("\n".join(lines))


if __name__ == "__main__":
    FlatternPattern().run()
