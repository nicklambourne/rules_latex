"""Measure safe staging and cache compression without changing either policy.

Usage: python build_support.py STAGING_BASE_ROOT SCRATCH_DIR
The staging baseline must include #106 (isolated copies and validated placements).
"""
import gzip
import importlib.util
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

root, scratch = map(lambda value: Path(value).resolve(), sys.argv[1:])
scratch.mkdir(parents=True, exist_ok=True)
spec = importlib.util.spec_from_file_location("staging", root / "tools/staging.py")
staging = importlib.util.module_from_spec(spec)
spec.loader.exec_module(staging)
original_cwd = Path.cwd()
with tempfile.TemporaryDirectory(prefix="build-support-", dir=scratch) as task_dir:
    task = Path(task_dir)
    try:
        for count, size in ((100, 2048), (10, 1024 * 1024)):
            sources = task / f"sources-{count}"
            sources.mkdir()
            for i in range(count):
                (sources / f"{i}.tex").write_bytes(b"x" * size)
            os.chdir(sources)
            files = [Path(f"{i}.tex") for i in range(count)]
            samples = []
            for trial in range(13):
                with tempfile.TemporaryDirectory(dir=task) as destination:
                    start = time.perf_counter()
                    staging.stage_sources(files[0], files, [], Path(destination))
                    samples.append((time.perf_counter() - start) * 1000)
            print(json.dumps({"staging_files": count, "file_bytes": size,
                              "median_ms": statistics.median(samples[2:])}), flush=True)
    finally:
        os.chdir(original_cwd)

archive = root / "examples/hello/hello_cache.tar.gz"
tar = gzip.decompress(archive.read_bytes())
for level in (1, 6, 9):
    samples = []
    for trial in range(5):
        start = time.perf_counter()
        compressed = gzip.compress(tar, compresslevel=level, mtime=0)
        samples.append((time.perf_counter() - start) * 1000)
    assert gzip.decompress(compressed) == tar
    print(json.dumps({"compression_level": level, "tar_bytes": len(tar),
                      "gzip_bytes": len(compressed),
                      "median_ms": statistics.median(samples)}), flush=True)
