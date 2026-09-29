"""Benchmark chunk generation and verify identical manifests and chunk bytes.

Usage: python chunks.py BASE_ROOT HEAD_ROOT SCRATCH_DIR REAL_PDF
Use a scratch directory on the intended build filesystem, with 250 MiB free.
"""
import dataclasses, importlib.util, json, shutil, statistics, sys, time, tracemalloc, tempfile
from pathlib import Path
(old, new, output, real) = map(Path, sys.argv[1:])
output.mkdir(parents=True, exist_ok=True)
task_temp = tempfile.TemporaryDirectory(prefix='chunks-bench-', dir=output)
output = Path(task_temp.name)

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m
modules = [load('old_chunks', old / 'tools/pdf_chunks.py'), load('new_chunks', new / 'tools/pdf_chunks.py')]
test = load('fixtures', old / 'tests/py/test_pdf_chunks.py')
fixtures = {'thesis': real.read_bytes(), 'large_stream': test._build_xref_stream_pdf([b'<< /Length 67108864 >>\nstream\n' + b'x' * (64 * 1024 * 1024) + b'\nendstream'], w=(1, 4, 2)), 'many_objects': test._build_xref_stream_pdf([f'<< /Value {i} >>'.encode() for i in range(5000)], w=(1, 4, 2))}
for (name, data) in fixtures.items():
    pdf = output / f'{name}.pdf'
    pdf.write_bytes(data)
    dirs = [output / (name + str(i)) for i in range(2)]
    manifests = [m.compute_manifest(pdf, d) for (m, d) in zip(modules, dirs)]
    assert all(manifests)
    assert dataclasses.asdict(manifests[0]) == dataclasses.asdict(manifests[1])
    for c in manifests[0].chunks:
        assert (dirs[0] / c.hash).read_bytes() == (dirs[1] / c.hash).read_bytes() == data[c.start:c.end]
    for (mode, repeats) in [('warm', 21), ('cold', 7)]:
        values = [[], []]
        for trial in range(repeats):
            for i in [0, 1] if trial % 2 else [1, 0]:
                if mode == 'cold':
                    shutil.rmtree(dirs[i], ignore_errors=True)
                start = time.perf_counter()
                result = modules[i].compute_manifest(pdf, dirs[i])
                values[i].append((time.perf_counter() - start) * 1000)
                assert result
        memory = []
        for (m, d) in zip(modules, dirs):
            if mode == 'cold':
                shutil.rmtree(d, ignore_errors=True)
            tracemalloc.start()
            m.compute_manifest(pdf, d)
            memory.append(tracemalloc.get_traced_memory()[1] / 1024 / 1024)
            tracemalloc.stop()
        print(json.dumps({'fixture': name, 'bytes': len(data), 'mode': mode, 'before_ms': statistics.median(values[0]), 'after_ms': statistics.median(values[1]), 'before_peak_mib': memory[0], 'after_peak_mib': memory[1]}), flush=True)
task_temp.cleanup()
