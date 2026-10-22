import os
import unittest

import pytest
import tomllib
from conftest import ROOT
from inkex.tester.inx import InxMixin


class InxTest(InxMixin, unittest.TestCase):
    def test_inx_matches_extension(self):
        self.assertInxIsGood(os.path.join(ROOT, "flatternpattern.inx"))


@pytest.mark.parametrize(
    ("name", "text"), [("flatternpattern.inx", "Version {}<"), ("README.md", "Version: {}.")]
)
def test_version_matches_pyproject(name, text):
    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
        version = tomllib.load(f)["project"]["version"]
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        assert text.format(version) in f.read()
