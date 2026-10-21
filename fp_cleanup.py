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


def remove_unused(svg, candidates):
    """Remove each element in candidates that nothing in svg refers to, for
    example a pattern or a mask.

    An element is in use when an element outside it refers to it or to an
    element in its content, for example a <use> in a tile copy. A reference
    from the content to the content itself does not count. The removal of an
    element can free the elements in <defs> that it refers to, for example the
    pattern that a wrapper inherits from or a clipPath in pattern content. Thus
    those elements become candidates too, and the function repeats until
    nothing changes."""
    refs = references(svg)
    inside: dict = {}  # the references and ids in each candidate, made once
    remaining = list(candidates)
    while True:
        for elem in remaining:
            if elem not in inside:
                inside[elem] = (references(elem), ids_in(elem))
        unused = [e for e in remaining if not any(refs[i] > inside[e][0][i] for i in inside[e][1])]
        if not unused:
            return
        for elem in unused:
            refs -= inside[elem][0]
            elem.getparent().remove(elem)
        remaining = [e for e in remaining if e not in unused]
        freed = defs_with_ids(svg, {i for e in unused for i in inside[e][0]})
        remaining += [e for e in freed if e not in remaining]


def defs_with_ids(svg, ids) -> list:
    """Return the elements in <defs> of svg that have one of the ids."""
    found = (svg.getElementById(i) for i in ids)
    return [e for e in found if e is not None and any(a.TAG == "defs" for a in e.ancestors())]
