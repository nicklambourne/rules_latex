"""Contract test for the private rules_latex Python runtime."""

import sys
import unittest


class PythonRuntimeTest(unittest.TestCase):
    def test_private_tools_use_python_3_13(self):
        self.assertEqual((3, 13), sys.version_info[:2])


if __name__ == "__main__":
    unittest.main()
