"""Unit tests for the editor-detection logic in serve_web.py.tpl.

The detection helpers live inside the template (so the generated
launcher is single-file). We load and substitute the template the same
way test_synctex_parser.py does, then exercise the public surface:
``detect_editor()``, ``editor_preview_uri()``, ``open_in_editor()``,
and ``open_in_browser()``.

We mock ``shutil.which``, ``subprocess.Popen``, and
``webbrowser.open`` rather than launching real editors / browsers — the
tests are about routing decisions, not about whether VS Code happens
to be installed on the CI runner.
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
    "{{PORT}}": "8765",
    "{{DOCUMENT_NAME}}": "doc",
    "{{PDFJS_LIB_RUNFILE}}": "_pdfjs/pdf.mjs",
    "{{PDFJS_WORKER_RUNFILE}}": "_pdfjs/pdf.worker.mjs",
    "{{OPEN_ON_START}}": "0",
}


def _load_template_module():
    """Substitute placeholders and import the resulting Python module.

    Same trick as test_synctex_parser._load_template_module(); we keep
    a separate copy here to avoid coupling the two test files.
    """
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
            "serve_web_editor_test_module", tmp.name
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules["serve_web_editor_test_module"] = module
        spec.loader.exec_module(module)
        return module
    finally:
        Path(tmp.name).unlink()


_M = _load_template_module()


class TestDetectEditor(unittest.TestCase):
    """``detect_editor()`` reads TERM_PROGRAM and maps to a known editor."""

    def test_detects_vscode(self):
        with mock.patch.dict(_M.os.environ, {"TERM_PROGRAM": "vscode"}, clear=False):
            editor = _M.detect_editor()
        self.assertIsNotNone(editor)
        self.assertEqual(editor.name, "VS Code")
        self.assertEqual(editor.scheme, "vscode")
        self.assertEqual(editor.cli, "code")

    def test_detects_cursor(self):
        with mock.patch.dict(_M.os.environ, {"TERM_PROGRAM": "cursor"}, clear=False):
            editor = _M.detect_editor()
        self.assertIsNotNone(editor)
        self.assertEqual(editor.name, "Cursor")
        self.assertEqual(editor.scheme, "cursor")
        self.assertEqual(editor.cli, "cursor")

    def test_detects_vscodium(self):
        with mock.patch.dict(_M.os.environ, {"TERM_PROGRAM": "vscodium"}, clear=False):
            editor = _M.detect_editor()
        self.assertIsNotNone(editor)
        self.assertEqual(editor.name, "VSCodium")
        self.assertEqual(editor.scheme, "vscodium")
        self.assertEqual(editor.cli, "codium")

    def test_detection_is_case_insensitive(self):
        # Some shells / launchers uppercase env vars.
        with mock.patch.dict(_M.os.environ, {"TERM_PROGRAM": "VSCode"}, clear=False):
            editor = _M.detect_editor()
        self.assertIsNotNone(editor)
        self.assertEqual(editor.name, "VS Code")

    def test_strips_whitespace(self):
        with mock.patch.dict(_M.os.environ, {"TERM_PROGRAM": "  vscode  "}, clear=False):
            editor = _M.detect_editor()
        self.assertIsNotNone(editor)

    def test_unknown_term_program_returns_none(self):
        with mock.patch.dict(
            _M.os.environ, {"TERM_PROGRAM": "Apple_Terminal"}, clear=False,
        ):
            self.assertIsNone(_M.detect_editor())

    def test_jetbrains_is_not_detected(self):
        # JetBrains IDEs set TERMINAL_EMULATOR, not TERM_PROGRAM, and
        # have no Simple Browser equivalent — they must fall through.
        env = {"TERMINAL_EMULATOR": "JetBrains-JediTerm"}
        env.pop("TERM_PROGRAM", None)
        with mock.patch.dict(_M.os.environ, env, clear=True):
            self.assertIsNone(_M.detect_editor())

    def test_unset_term_program_returns_none(self):
        env = {}
        with mock.patch.dict(_M.os.environ, env, clear=True):
            self.assertIsNone(_M.detect_editor())


class TestEditorPreviewURI(unittest.TestCase):
    """``editor_preview_uri()`` builds correctly-encoded URIs."""

    def test_vscode_uri_shape(self):
        editor = _M._EDITORS_BY_TERM_PROGRAM["vscode"]
        uri = _M.editor_preview_uri(editor, "http://127.0.0.1:8765/")
        self.assertEqual(
            uri,
            "vscode://vscode.simpleBrowser/show?url="
            "http%3A%2F%2F127.0.0.1%3A8765%2F",
        )

    def test_cursor_scheme(self):
        editor = _M._EDITORS_BY_TERM_PROGRAM["cursor"]
        uri = _M.editor_preview_uri(editor, "http://127.0.0.1:8765/")
        self.assertTrue(uri.startswith("cursor://vscode.simpleBrowser/show?url="))

    def test_url_is_percent_encoded(self):
        # Path, query, and reserved chars must all be encoded so the
        # editor's URI dispatcher sees a single opaque `url=...` query
        # value rather than its own embedded fragments.
        editor = _M._EDITORS_BY_TERM_PROGRAM["vscode"]
        uri = _M.editor_preview_uri(editor, "http://127.0.0.1:8765/path?a=1&b=2")
        # The colon, slashes, ampersand, and equals in the inner URL
        # should all be encoded.
        self.assertNotIn("://127", uri.split("url=", 1)[1])
        self.assertNotIn("&", uri.split("url=", 1)[1])
        self.assertIn("%3A", uri)
        self.assertIn("%2F", uri)
        self.assertIn("%26", uri)


class TestOpenInEditor(unittest.TestCase):
    """``open_in_editor()`` launches the editor CLI or fails gracefully."""

    def test_returns_false_if_cli_missing(self):
        editor = _M._EDITORS_BY_TERM_PROGRAM["vscode"]
        with mock.patch.object(_M.shutil, "which", return_value=None):
            self.assertFalse(_M.open_in_editor(editor, "http://x/"))

    def test_invokes_cli_with_open_url(self):
        editor = _M._EDITORS_BY_TERM_PROGRAM["vscode"]
        with mock.patch.object(
            _M.shutil, "which", return_value="/usr/local/bin/code",
        ), mock.patch.object(_M.subprocess, "Popen") as popen:
            ok = _M.open_in_editor(editor, "http://127.0.0.1:8765/")
        self.assertTrue(ok)
        args, _kwargs = popen.call_args
        cmd = args[0]
        self.assertEqual(cmd[0], "/usr/local/bin/code")
        self.assertEqual(cmd[1], "--open-url")
        # The third argument is the encoded vscode:// URI; check its shape.
        self.assertTrue(cmd[2].startswith("vscode://vscode.simpleBrowser/show?url="))

    def test_returns_false_on_oserror(self):
        editor = _M._EDITORS_BY_TERM_PROGRAM["vscode"]
        with mock.patch.object(
            _M.shutil, "which", return_value="/usr/local/bin/code",
        ), mock.patch.object(
            _M.subprocess, "Popen", side_effect=OSError("nope"),
        ):
            self.assertFalse(_M.open_in_editor(editor, "http://x/"))


class TestOpenInBrowser(unittest.TestCase):
    """``open_in_browser()`` shells out to the stdlib ``webbrowser``."""

    def test_delegates_to_webbrowser_open(self):
        with mock.patch.object(_M.webbrowser, "open", return_value=True) as wb:
            self.assertTrue(_M.open_in_browser("http://127.0.0.1:8765/"))
        wb.assert_called_once()
        # `new=2` opens a new tab where supported; we just verify the
        # URL made it through unmodified.
        args, _kwargs = wb.call_args
        self.assertEqual(args[0], "http://127.0.0.1:8765/")

    def test_webbrowser_error_is_swallowed(self):
        with mock.patch.object(
            _M.webbrowser, "open", side_effect=_M.webbrowser.Error("nope"),
        ):
            self.assertFalse(_M.open_in_browser("http://x/"))


if __name__ == "__main__":
    unittest.main()
