"""Unit tests for open_in_browser() in serve_web.py.tpl.

The helper lives inside the template (so the generated launcher stays
single-file). We load and substitute the template the same way
test_synctex_parser.py does, then exercise open_in_browser, mocking
``webbrowser.open`` rather than launching a real browser — the test is
about the routing decision, not whether a browser is installed on CI.

(The previous VS Code "Simple Browser" auto-open path was removed: the
built-in Simple Browser has no URI handler, so the `code --open-url
vscode://...` handoff never worked. open_on_start now just opens the
system browser; see DESIGN.md §4.8.)
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


_TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "latex"
    / "private"
    / "serve_web.py.tpl"
)
_PLACEHOLDERS = {
    "{{DOCUMENT_LABEL}}": "//test:doc",
    "{{PDF_RELPATH}}": "test/doc.pdf",
    "{{SYNCTEX_RELPATH}}": "test/doc.synctex.gz",
    "{{WATCHED_PATHS}}": "test/doc.tex",
    "{{POLL_INTERVAL}}": "250",
    "{{DEBOUNCE_MS}}": "250",
    "{{DEBOUNCE_MAX_MS}}": "1500",
    "{{PORT}}": "8765",
    "{{DOCUMENT_NAME}}": "doc",
    "{{PDFJS_LIB_RUNFILE}}": "_pdfjs/pdf.mjs",
    "{{PDFJS_WORKER_RUNFILE}}": "_pdfjs/pdf.worker.mjs",
    "{{OPEN_ON_START}}": "0",
    "{{PDF_CHUNKS_RUNFILE}}": "_tools/pdf_chunks.py",
    "{{ENABLE_SERVE_CACHE}}": "",
    "{{SERVE_CACHE_RUNFILE}}": "",
    "{{PRIME_MAIN_RUNFILE}}": "",
    "{{PRIME_TECTONIC_RUNFILE}}": "",
    "{{PRIME_POPULATE_TOOL_RUNFILE}}": "",
    "{{PRIME_STAGING_LIB_RUNFILE}}": "",
    "{{PRIME_BIBER_RUNFILE}}": "",
    "{{PRIME_USE_SYSTEM_BIBER}}": "",
    "{{PRIME_SRCS}}": "",
    "{{PRIME_PKG_FILES}}": "",
}


def _load_template_module():
    """Substitute placeholders and import the resulting Python module."""
    source = _TEMPLATE_PATH.read_text()
    for placeholder, replacement in _PLACEHOLDERS.items():
        source = source.replace(placeholder, replacement)

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    )
    try:
        tmp.write(source)
        tmp.close()
        spec = importlib.util.spec_from_file_location(
            "serve_web_open_browser_test_module", tmp.name
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules["serve_web_open_browser_test_module"] = module
        spec.loader.exec_module(module)
        return module
    finally:
        Path(tmp.name).unlink()


_M = _load_template_module()


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
