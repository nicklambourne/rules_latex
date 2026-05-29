"""Load `latex/private/serve_web.py.tpl` as an importable module.

The serve_web Python code lives in a `.py.tpl` template that the
`latex_live` rule expands via `ctx.actions.expand_template`
substitutions. To unit-test the helpers inside it (BuildState,
_combine_output, etc.) without spinning up the full rule, we read
the template, substitute placeholders with safe defaults, write
the result to a temp `.py` file, and import it with importlib.

A real .py file is needed (vs `exec()` into a globals dict) so
dataclass introspection of `__module__` works correctly — without
it, `@dataclass` decorators raise at parse time.

The placeholder set must stay in sync with the substitution map in
`latex/private/latex_live.bzl`; new placeholders added to the
template need a default here. The defaults are deliberately
benign: empty strings for the implicit-pipeline-only fields,
numerals for the int-coerced ones, sentinel paths for the
runfile-style ones. Tests that care about a specific value
override it via the ``extra`` mapping argument.

This module is intentionally test-only — the `_` prefix excludes
it from accidental imports by the production code, and it lives
under `tests/py/` so the per-test sh_test data dep set picks it up
naturally.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from typing import Optional


_TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "latex"
    / "private"
    / "serve_web.py.tpl"
)

# Keep alphabetised for grep-ability. Defaults mirror what
# latex_live.bzl would produce for a minimal latex_document
# target with no cache configured.
_PLACEHOLDERS = {
    "{{DEBOUNCE_MAX_MS}}": "1500",
    "{{DEBOUNCE_MS}}": "250",
    "{{DOCUMENT_LABEL}}": "//test:doc",
    "{{DOCUMENT_NAME}}": "doc",
    "{{ENABLE_SERVE_CACHE}}": "",
    "{{OPEN_ON_START}}": "0",
    "{{PDF_CHUNKS_RUNFILE}}": "_tools/pdf_chunks.py",
    "{{PDFJS_LIB_RUNFILE}}": "_pdfjs/pdf.mjs",
    "{{PDFJS_WORKER_RUNFILE}}": "_pdfjs/pdf.worker.mjs",
    "{{PDF_RELPATH}}": "test/doc.pdf",
    "{{POLL_INTERVAL}}": "250",
    "{{PORT}}": "8765",
    "{{PRIME_BIBER_RUNFILE}}": "",
    "{{PRIME_MAIN_RUNFILE}}": "",
    "{{PRIME_PKG_FILES}}": "",
    "{{PRIME_POPULATE_TOOL_RUNFILE}}": "",
    "{{PRIME_SRCS}}": "",
    "{{PRIME_STAGING_LIB_RUNFILE}}": "",
    "{{PRIME_TECTONIC_RUNFILE}}": "",
    "{{PRIME_USE_SYSTEM_BIBER}}": "",
    "{{SERVE_CACHE_RUNFILE}}": "",
    "{{SERVE_WEB_ASSETS}}": "",
    "{{SYNCTEX_RELPATH}}": "test/doc.synctex.gz",
    "{{WATCHED_PATHS}}": "test/doc.tex",
    "{{WS_SERVER_RUNFILE}}": "_tools/ws_server.py",
}


def load_template_module(
    name: str = "serve_web_test_module",
    extra: Optional[dict[str, str]] = None,
):
    """Substitute placeholders, importlib-load, return the module.

    ``name`` lets concurrent tests register the module under their
    own key in ``sys.modules`` so they don't trample each other's
    state.

    ``extra`` overrides individual placeholder values for a single
    test (e.g. to flip ENABLE_SERVE_CACHE on). Unknown keys raise
    KeyError so typos fail loudly.
    """
    overrides = dict(_PLACEHOLDERS)
    if extra:
        for k, v in extra.items():
            if k not in overrides:
                raise KeyError(f"unknown placeholder {k!r}")
            overrides[k] = v

    source = _TEMPLATE_PATH.read_text()
    for placeholder, replacement in overrides.items():
        source = source.replace(placeholder, replacement)

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8",
    )
    try:
        tmp.write(source)
        tmp.close()
        spec = importlib.util.spec_from_file_location(name, tmp.name)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        Path(tmp.name).unlink()
