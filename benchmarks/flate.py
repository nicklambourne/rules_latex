"""Compare bounded PDF stream decoding: python flate.py BASE_ROOT HEAD_ROOT."""
import importlib.util
import json
import statistics
import sys
import time
import tracemalloc
import zlib
from pathlib import Path


def load(name, root):
    spec = importlib.util.spec_from_file_location(name, Path(root) / "tools/pdf_chunks.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


modules = [load("before", sys.argv[1]), load("after", sys.argv[2])]
for size in (256, 200000):
    expected = b"x" * size
    payload = zlib.compress(expected)
    samples = [[], []]
    for trial in range(21):
        for i in ([0, 1] if trial % 2 else [1, 0]):
            start = time.perf_counter()
            for _ in range(100):
                assert modules[i]._decompress_bounded(payload) == expected
            samples[i].append((time.perf_counter() - start) * 10)
    peaks = []
    for module in modules:
        tracemalloc.start()
        module._decompress_bounded(payload)
        peaks.append(tracemalloc.get_traced_memory()[1] / 1024 / 1024)
        tracemalloc.stop()
    print(json.dumps({"decoded_bytes": size, "before_ms": statistics.median(samples[0]),
                      "after_ms": statistics.median(samples[1]),
                      "before_peak_mib": peaks[0], "after_peak_mib": peaks[1]}))
