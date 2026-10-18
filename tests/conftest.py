import os
import shutil
import sys

import pytest

# The tests use the inkex package that Inkscape bundles, not a PyPI copy.
EXT_DIR = os.environ.get("INKSCAPE_EXTENSIONS_DIR", "/usr/share/inkscape/extensions")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(1, EXT_DIR)

DATA = os.path.join(ROOT, "tests", "data")


def load(data: bytes):
    """Parse SVG bytes, for example the output of an extension run, into a root element."""
    import io

    import inkex  # here, not at the top: inkex is on sys.path only after the lines above

    return inkex.load_svg(io.BytesIO(data)).getroot()


INKSCAPE = os.environ.get("INKSCAPE_COMMAND", "inkscape")


def pytest_collection_modifyitems(config, items):
    if shutil.which(INKSCAPE):
        return
    skip = pytest.mark.skip(reason="the inkscape executable is not on PATH")
    for item in items:
        if "inkscape" in item.keywords:
            item.add_marker(skip)
