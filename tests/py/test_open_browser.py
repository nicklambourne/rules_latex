"""Unit tests for open_in_browser() in serve_web_runtime.py.

We import the server runtime and exercise open_in_browser, mocking
``webbrowser.open`` rather than launching a real browser. The test is
about the routing decision, not whether a browser is installed on CI.

(The previous VS Code "Simple Browser" auto-open path was removed: the
built-in Simple Browser has no URI handler, so the `code --open-url
vscode://...` handoff never worked. open_on_start now just opens the
system browser; see DESIGN.md §4.8.)
"""

from __future__ import annotations

import unittest
from unittest import mock

from tests.py._server_loader import load_server_module


_M = load_server_module("serve_web_open_browser_test_module")


class TestOpenInBrowser(unittest.TestCase):
    """open_in_browser() shells out to the stdlib webbrowser."""

    def test_delegates_to_webbrowser_open(self):
        with mock.patch.object(_M.webbrowser, "open", return_value=True) as wb:
            self.assertTrue(_M.open_in_browser("http://127.0.0.1:8765/"))
        wb.assert_called_once()
        # `new=2` opens a new tab where supported; we just verify the URL
        # made it through unmodified.
        args, _kwargs = wb.call_args
        self.assertEqual(args[0], "http://127.0.0.1:8765/")

    def test_webbrowser_error_is_swallowed(self):
        with mock.patch.object(
            _M.webbrowser, "open", side_effect=_M.webbrowser.Error("nope"),
        ):
            self.assertFalse(_M.open_in_browser("http://x/"))


if __name__ == "__main__":
    unittest.main()
