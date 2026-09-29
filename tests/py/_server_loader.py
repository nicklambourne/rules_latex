"""Import an isolated live-preview runtime with test-safe configuration."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Optional

_RUNTIME_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "latex"
    / "private"
    / "serve_web_runtime.py"
)

_DEFAULT_CONFIG = {
    "COMPILE_TOOL_RUNFILE": "",
    "DEBOUNCE_MAX_MS": 1500,
    "DEBOUNCE_MS": 250,
    "DOCUMENT_LABEL": "//test:doc",
    "DOCUMENT_NAME": "doc",
    "ENABLE_SERVE_CACHE": False,
    "LOGO_RUNFILE": "_assets/logo.svg",
    "OPEN_ON_START": False,
    "PDF_CHUNKS_RUNFILE": "_tools/pdf_chunks.py",
    "PDFJS_LIB_RUNFILE": "_pdfjs/pdf.mjs",
    "PDFJS_WORKER_RUNFILE": "_pdfjs/pdf.worker.mjs",
    "PDF_RELPATH": "test/doc.pdf",
    "POLL_INTERVAL_MS": 250,
    "PORT": 8765,
    "PRIME_BIBER_RUNFILE": "",
    "PRIME_BUNDLE_URL": "",
    "PRIME_BUNDLE_MANIFEST_RUNFILE": "",
    "PRIME_CTAN_LOCK_RUNFILE": "",
    "PRIME_CTAN_PACKAGES_RAW": "",
    "PRIME_MAIN_RUNFILE": "",
    "PRIME_PKG_FILES_RAW": "",
    "PRIME_POPULATE_TOOL_RUNFILE": "",
    "PRIME_SRCS_RAW": "",
    "PRIME_STAGING_LIB_RUNFILE": "",
    "PRIME_TECTONIC_RUNFILE": "",
    "PRIME_USE_SYSTEM_BIBER": False,
    "SERVE_CACHE_RUNFILE": "",
    "SERVE_FAST": False,
    "SERVE_WEB_ASSETS": "",
    "SYNCTEX_RELPATH": "test/doc.synctex.gz",
    "WATCHED_PATHS_RAW": "test/doc.tex",
    "WS_SERVER_RUNFILE": "_tools/ws_server.py",
}


def load_server_module(
    name: str = "serve_web_test_module",
    extra: Optional[dict[str, object]] = None,
):
    """Load the runtime under a unique module name and configure it."""
    config = dict(_DEFAULT_CONFIG)
    if extra:
        if unknown := set(extra) - set(config):
            raise KeyError(f"unknown config fields {unknown}")
        config.update(extra)

    spec = importlib.util.spec_from_file_location(name, _RUNTIME_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    module.configure(config)
    return module
