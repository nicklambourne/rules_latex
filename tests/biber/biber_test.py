"""Exercise the actual pinned macOS executable, without Xcode or a warm cache."""

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import platform
import struct
import subprocess
import sys
import tempfile
import unittest


BIBER = Path(sys.argv.pop(1)).resolve()
VERSION = sys.argv.pop(1)


class BiberTest(unittest.TestCase):
    def test_native_thin_binary(self):
        with BIBER.open("rb") as binary:
            magic, cpu = struct.unpack("<II", binary.read(8))
        self.assertEqual(magic, 0xFEEDFACF, "must not restore the universal lipo bootstrap")
        self.assertEqual(cpu, {"arm64": 0x100000C, "x86_64": 0x1000007}[platform.machine()])

    def test_concurrent_cold_start_without_xcode(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {k: v for k, v in os.environ.items() if not k.startswith("PAR_")}
            env.update(TMPDIR=tmp, PATH="/usr/bin:/bin", DEVELOPER_DIR=tmp + "/no-xcode")

            def run(_):
                return subprocess.run([str(BIBER), "--version"], env=env,
                                      capture_output=True, text=True, timeout=60)

            # All invocations share a fresh PAR extraction cache, as parallel
            # document actions can do. No system Perl installation is needed.
            with ThreadPoolExecutor(max_workers=3) as pool:
                results = list(pool.map(run, range(3)))
            for result in results:
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "biber version: " + VERSION)


if __name__ == "__main__":
    unittest.main()
