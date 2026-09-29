"""File-to-sink benchmark: python streaming.py BASE_ROOT HEAD_ROOT SCRATCH_DIR.

Reports server-side file read/dispatch time and peak traced Python allocations;
excludes HTTP socket transfer and browser rendering. Uses disposable scratch.
"""
import importlib.util, json, statistics, sys, time, tracemalloc, tempfile
from pathlib import Path
(old, new, scratch) = map(Path, sys.argv[1:])
scratch.mkdir(parents=True, exist_ok=True)
task_temp = tempfile.TemporaryDirectory(prefix='streaming-bench-', dir=scratch)
output = Path(task_temp.name)
(output / 'bazel-bin/test').mkdir(parents=True)

def load(name, root):
    s = importlib.util.spec_from_file_location(name, root / 'tests/py/_template_loader.py')
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m.load_template_module(name + 'server')
modules = [load('before', old), load('after', new)]

class Sink:

    def __init__(self):
        self.bytes = 0

    def write(self, data):
        self.bytes += len(data)

def run(m, headers, head=False):
    h = m.Handler.__new__(m.Handler)
    h.workspace = output
    h.headers = headers
    h._head_mode = head
    h.wfile = Sink()
    h.send_response = lambda *a: None
    h.send_header = lambda *a: None
    h.end_headers = lambda : None
    h._serve_pdf_with_range()
    return h.wfile.bytes
for size in [1024 * 1024, 64 * 1024 * 1024]:
    (output / 'bazel-bin/test/doc.pdf').write_bytes(b'x' * size)
    for (kind, headers, head) in [('get', {}, False), ('range', {'Range': f'bytes=123-{size - 124}'}, False), ('head', {}, True)]:
        samples = [[], []]
        peaks = []
        for i in range(23):
            for j in [0, 1] if i % 2 else [1, 0]:
                start = time.perf_counter()
                run(modules[j], headers, head)
                elapsed = (time.perf_counter() - start) * 1000
                if i >= 2:
                    samples[j].append(elapsed)
        for m in modules:
            tracemalloc.start()
            run(m, headers, head)
            peaks.append(tracemalloc.get_traced_memory()[1] / 1024 / 1024)
            tracemalloc.stop()
        print(json.dumps({'size_mib': size // 1024 // 1024, 'kind': kind, 'before_ms': statistics.median(samples[0]), 'after_ms': statistics.median(samples[1]), 'before_peak_mib': peaks[0], 'after_peak_mib': peaks[1]}), flush=True)
task_temp.cleanup()
