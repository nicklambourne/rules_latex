#!/usr/bin/env python3
"""Per-target launcher for the importable live-preview server runtime."""

from __future__ import annotations

import json
import sys
from pathlib import Path

RUNTIME_RUNFILE = {{SERVER_RUNTIME_RUNFILE}}
CONFIG_RUNFILE = {{SERVER_CONFIG_RUNFILE}}


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: serve_web.py <workspace_dir> <runfiles_dir>", file=sys.stderr)
        return 2
    runfiles = Path(sys.argv[2]).resolve()
    sys.path.insert(0, str((runfiles / RUNTIME_RUNFILE).parent))
    import serve_web_runtime

    config = json.loads((runfiles / CONFIG_RUNFILE).read_text())
    serve_web_runtime.configure(config)
    return serve_web_runtime.main()


if __name__ == "__main__":
    sys.exit(main())
