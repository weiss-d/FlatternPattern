import os
import sys

# The tests use the inkex package that Inkscape bundles, not a PyPI copy.
EXT_DIR = os.environ.get("INKSCAPE_EXTENSIONS_DIR", "/usr/share/inkscape/extensions")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(1, EXT_DIR)

DATA = os.path.join(ROOT, "tests", "data")
