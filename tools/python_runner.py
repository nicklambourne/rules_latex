"""Run a Python source file with rules_latex's pinned interpreter."""

import runpy
import sys


def main():
    if len(sys.argv) < 2:
        raise SystemExit("usage: python_runner <script> [args ...]")

    sys.argv = sys.argv[1:]
    runpy.run_path(sys.argv[0], run_name="__main__")


if __name__ == "__main__":
    main()
