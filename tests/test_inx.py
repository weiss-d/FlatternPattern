import os
import unittest

from conftest import ROOT
from inkex.tester.inx import InxMixin


class InxTest(InxMixin, unittest.TestCase):
    def test_inx_matches_extension(self):
        self.assertInxIsGood(os.path.join(ROOT, "flatternpattern.inx"))
