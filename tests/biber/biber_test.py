"""Exercise the actual pinned macOS executable, without Xcode or a warm cache."""

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import platform
import struct
import subprocess
import sys
import tempfile
import threading
import unittest


BIBER = Path(sys.argv.pop(1)).resolve()
VERSION = sys.argv.pop(1)
TOOLS = Path(__file__).resolve().parents[2] / "tools"

# Separate Python processes exercise the real compile/populate integration,
# including the cross-process initialization lock and hermetic interpreter.
RUN_WRAPPER = """
import importlib, pathlib, sys
sys.path.insert(0, sys.argv[1])
tool = importlib.import_module(sys.argv[2])
work = pathlib.Path(sys.argv[3])
kwargs = dict(tectonic=work.parent / 'tectonic', main_in_workdir=work / 'main.tex',
              cache_dir=work, biber=pathlib.Path(sys.argv[4]))
if sys.argv[2] == 'tectonic_compile':
    kwargs.update(bundle=None, outfmt='pdf', synctex=False, reproducible=False, extra_args=[])
tool.run_tectonic(**kwargs)
"""


class BiberTest(unittest.TestCase):
    def test_native_thin_binary(self):
        with BIBER.open("rb") as binary:
            magic, cpu = struct.unpack("<II", binary.read(8))
        self.assertEqual(magic, 0xFEEDFACF, "must not restore the universal lipo bootstrap")
        self.assertEqual(cpu, {"arm64": 0x100000C, "x86_64": 0x1000007}[platform.machine()])

    def test_concurrent_cold_start_without_xcode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {k: v for k, v in os.environ.items() if not k.startswith("PAR_")}
            env.update(TMPDIR=tmp, PATH="/usr/bin:/bin", DEVELOPER_DIR=tmp + "/no-xcode")
            # The pinned toolchain must not use a caller-selected shared cache.
            env["PAR_GLOBAL_TEMP"] = str(root / "unmanaged-cache")
            tectonic = root / "tectonic"
            tectonic.write_text('#!/bin/sh\nprintf "%s\\n" "$PAR_GLOBAL_TEMP" > par-cache.txt\n'
                                'biber --version > biber-version.txt\n')
            tectonic.chmod(0o755)
            barrier = threading.Barrier(3)

            def run(i):
                work = root / str(i)
                work.mkdir()
                (work / "main.tex").touch()
                tool = "tectonic_compile" if i % 2 else "tectonic_populate_cache"
                barrier.wait(timeout=30)
                return subprocess.run([sys.executable, "-c", RUN_WRAPPER,
                                       str(TOOLS), tool, str(work), str(BIBER)], env=env,
                                      capture_output=True, text=True, timeout=120)

            # Both a cold concurrent start and reuse of the same warmed cache.
            # No system Perl installation is needed, and failures are not retried.
            for start in (0, 3):
                with ThreadPoolExecutor(max_workers=3) as pool:
                    results = list(pool.map(run, range(start, start + 3)))
                for result in results:
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            caches = set()
            for i in range(6):
                self.assertEqual((root / str(i) / "biber-version.txt").read_text().strip(),
                                 "biber version: " + VERSION)
                caches.add((root / str(i) / "par-cache.txt").read_text().strip())
            self.assertEqual(len(caches), 1, "must retain warm cache reuse across actions")
            self.assertFalse((root / "unmanaged-cache").exists())


if __name__ == "__main__":
    unittest.main()
