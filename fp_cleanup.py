"""Removal of the pattern definitions that the extension no longer needs."""

from collections import Counter

from fp_svg import URL_RE


def references(root) -> Counter:
    """Return how often root and its descendants refer to each id: url(#id) in
    attributes and style sheets, and #id in href and xlink:href."""
    found = Counter()
    for elem in root.iter():
        if not isinstance(elem.tag, str):
            continue
        for name, value in elem.attrib.items():
            if name.endswith("href") and value.startswith("#"):
                found[value[1:]] += 1
            elif "url(" in value:
                found.update(URL_RE.findall(value))
        if elem.TAG == "style" and elem.text:
            found.update(URL_RE.findall(elem.text))
    return found


def ids_in(pattern) -> set[str]:
    """Return the ids of pattern and of the elements in its content."""
    return {e.get("id") for e in pattern.iter() if isinstance(e.tag, str) and e.get("id")}


def remove_unused(svg, patterns):
    """Remove each pattern element in patterns that nothing in svg refers to.

    A pattern is in use when an element outside it refers to it or to an
    element in its content, for example a <use> in a tile copy. A reference from
    the pattern content to the content itself does not count. The removal of a
    wrapper pattern can free the pattern that it inherits from, so the function
    repeats until nothing changes."""
    refs = references(svg)
    remaining = list(patterns)
    while True:
        unused = [p for p in remaining if not in_use(p, refs)]
        if not unused:
            return
        for pattern in unused:
            refs -= references(pattern)
            pattern.getparent().remove(pattern)
        remaining = [p for p in remaining if p not in unused]


def in_use(pattern, refs: Counter) -> bool:
    """Return True when refs holds a reference to pattern or its content from
    an element outside pattern."""
    inside = references(pattern)
    return any(refs[i] > inside[i] for i in ids_in(pattern))
