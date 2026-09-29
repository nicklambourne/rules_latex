# Performance review and PR plan — 2026-09-28

## Snapshot-retention follow-up

Historical sequence: the measurements below preceded the transport-cancellation
fix (#127). That follow-up adds a delayed-response regression and real-browser
coverage for deliveries to retired PDF.js readers. The integration resolutions
have now been published as an ordered train through #127; the earlier branching
merge order and local-only integration notes below describe the original plan.
No PR is merged by these validation steps.

The subsequent all-33-PR end-to-end audit found that immutable PDF snapshots
were never collected. Keep the correctness fixes and streaming transport; bound
published snapshots to 8 files / 128 MiB / five minutes, always retaining the
current generation (an oversized current PDF is retained alone).

Validation on the combined train plus retention fix, on the same local host:

- All 39 Bazel test targets passed. Coverage includes restart cleanup, capacity
  overriding grace, oversized current PDFs, unsupported/failed parsing, and an
  HTTP reader completing with the original bytes after its snapshot is unlinked.
- Real Chrome recovered from an injected `410` on a generation URL and rendered
  the expected pixels. A separate 12 MiB image-PDF run passed 20 text edits and
  an image-only edit; every edit changed the visible pixels. Snapshot storage
  fell from 27 files / 324.4 MiB to one current PDF on startup, then plateaued
  at 8 files / 96.1 MiB. All sampled current generations remained available.
- 80 alternating paired post-build-hook measurements on that PDF: median
  **19.04 → 19.54 ms (+0.50 ms / 2.6%)**. Cleanup itself took **0.35 ms** median,
  **0.53 ms** p95. This is a small added cost, not a measured end-to-end speedup.
  Both variants began each sample with eight snapshots; the baseline's history
  was trimmed outside its timer. Warm chunk caches, no connected clients.
- The 20-edit end-to-end run had a 1.75 s median edit-to-paint latency. It was
  not a paired timing comparison and ran on a shared host. One previously
  observed PDF.js startup cancellation warning recurred and recovered; the
  retention change does not claim to resolve that separate diagnostic.

No correctness fix was reverted and no PR was merged. When combining with
#118, apply the server changes to `serve_web_runtime.py`, as validated locally.

## Combined end-to-end results

The complete original 33-PR train (#93–125) was compared with v0.7.0 on
28 September 2026: Apple M4, macOS 26.6.2, Bazel 8.0.0, pinned Python 3.13,
and headless Chrome. Dependencies, filesystem caches, and document snapshots
were warm. The host was shared, so these are observations rather than
statistically established speedups or regressions; no per-PR attribution was
measured. The later retention/cancellation fixes were validated separately.

Seven alternating matched source variants per document in the tighter build
confirmation batch gave these median **complete Bazel build** times:

| Workload | v0.7.0 | Combined train | Change |
| --- | ---: | ---: | ---: |
| One page | 0.747 s | 0.739 s | -1.0% |
| 100 pages | 0.847 s | 0.852 s | +0.6% |
| 4 pages / 12 MiB images | 1.197 s | 1.225 s | +2.4% |
| 7-page thesis / Biber | 3.252 s | 3.230 s | -0.7% |

Preserve safe source copies despite the observed image-heavy cost (about
29 ms using the unrounded medians). The association with copying is plausible,
not isolated by an ablation. Across initial and confirmation batches, all
124 matched before/after output pairs were byte-identical. No-change cache
hits were about 60–63 ms and are not comparable to edited-source builds.

Real-browser **source-save to first visible repaint** includes polling,
debounce, compilation, transfer, and rendering. Two default-preview sessions
per side produced 12 matched edits for the small fixture and 14 for each other
fixture; fast mode used ten edits per side:

| Workload | Default: release → train | Fast: release → train |
| --- | ---: | ---: |
| One page | 1.284 → 1.446 s | 1.144 → 1.154 s |
| 100 pages | 2.460 → 2.048 s | 1.253 → 1.240 s |
| 4 pages / 12 MiB images | 2.159 → 2.179 s | 1.628 → 1.637 s |
| 7-page thesis / Biber | 3.854 → 3.833 s | Not measured |

All 86 paired edit canvas checksums matched. Image-only edits rendered stale
pixels in the release (2/2) and correct pixels in the train (2/2). Conservative
PDF invalidation fixes that error while viewport gating keeps rendering bounded.
Do not interpret the variable default-preview percentages as established
performance changes, or component allocation savings as lower total server RSS.

Bibliography measurements used the same ARM64 slice of the pinned Biber binary
on both sides to work around this host's universal-launcher failure. Network-cold
downloads, other OS/Bazel combinations, remote execution, concurrent preview
clients, and long-duration memory stress were not performance-tested. The
bounded-retention follow-up's extra 0.50 ms median hook cost is reported above;
its unpaired edit timings are not a new before/after comparison.

## Original performance review

The review covered source staging, compilation and cache handling, PDF snapshot
and chunk generation, HTTP transport, browser chunk loading/range planning,
rendering, watcher/status polling, and CI. The five changes below remove measured
work without weakening the audit's isolation, size limits or render invalidation.
They do not change public Bazel attributes or require consumer configuration.

## Proposed train

| Priority | PR | Change | Base / validation focus |
| --- | --- | --- | --- |
| 1 | [#122](https://github.com/nicklambourne/rules_latex/pull/122) | Remove unnecessary full-budget inflate allocation | #111; size limits, exact-limit streams, corruption, truncation, peak allocation |
| 2 | [#125](https://github.com/nicklambourne/rules_latex/pull/125) | Stream PDF GET/Range; skip HEAD reads | #114 (includes #101); range bytes, generation consistency, bounded reads, browser reload |
| 3 | [#123](https://github.com/nicklambourne/rules_latex/pull/123) | Hash/write buffer views; stat existing chunks once | #122; identical manifests, chunk bytes, atomic writes and deduplication |
| 4 | [#124](https://github.com/nicklambourne/rules_latex/pull/124) | Share concurrent loads of the same chunk | #121; retries, cache limits, independent hashes, browser reload |
| 5 | [#121](https://github.com/nicklambourne/rules_latex/pull/121) | Binary search into sorted, disjoint chunk ranges | #111; exhaustive boundary coverage and logarithmic access count |

Merge dependency order is #111 → #122 → #123 and #111 → #121 → #124;
#101 → #114 → #125 is the server path. Review priority is not merge order.
Keep all five unmerged pending approval. When integrating #118's server-module
extraction, preserve #125's handler in the extracted runtime. The browser merge
must preserve #100's recovery state, #111's byte budget, #101's immutable URLs and
#96's conservative resource invalidation; the local integration test combined
all those changes successfully.

## Measurements

Local Apple Silicon/macOS 26.6.2, Python 3.13.13 from rules_python, Node 26.3.1,
warm filesystem caches. These are component measurements, not an end-to-end
compile/render speedup forecast. Peak memory means Python allocations recorded by
tracemalloc, not process RSS. Do not add the independently measured savings.

| Operation / workload | Before | After | Interpretation |
| --- | ---: | ---: | --- |
| Decode a 256-byte Flate stream, peak allocation | 64.007 MiB | 0.039 MiB | Removes the accidental large temporary buffer |
| Decode 200,000 bytes, peak allocation | 64.038 MiB | 0.573 MiB | Same bounded output and EOF checks |
| Serve 64 MiB PDF to sink | 5.17 ms / 64.01 MiB | 4.05 ms / 2.01 MiB | Bounded body blocks, independent of response size |
| HEAD of 64 MiB PDF | 4.88 ms / 64.01 MiB | 0.023 ms / 0.006 MiB | No body reads |
| Serve 1 MiB PDF to sink | 0.044 ms | 0.044 ms | No small-response penalty measured |
| Chunk 64 MiB object, warm | 28.65 ms / 128.00 MiB | 26.80 ms / 64.04 MiB | Removes object-sized copies; still reads the original PDF |
| Chunk 64 MiB object, cold | 53.19 ms | 48.75 ms | Same immutable chunk files |
| Chunk 5,000 small objects, warm | 37.63 ms | 31.24 ms | Fewer filesystem metadata calls |
| Chunk real 260 KiB thesis PDF, warm | 0.920 ms | 0.884 ms | Small absolute benefit for ordinary PDFs |
| Eight concurrent misses for one 1 MiB chunk | 8 requests / 8 MiB / 28.01 ms | 1 request / 1 MiB / 23.26 ms | Controlled 20 ms-delay HTTP fixture |
| One chunk consumer, same fixture | 23.34 ms | 23.08 ms | No single-consumer penalty measured |
| Range lookup across 1,000 objects | 0.531 µs | 0.067 µs | Distributed range starts, 2,000 requests per batch |
| Range lookup across 100,000 objects | 59.817 µs | 0.306 µs | Better scaling, not a typical document prediction |

**Regressions / uncertainty:** cold creation of 5,000 tiny chunk files measured
454.06 → 455.84 ms (+0.4%, 1.8 ms). Decode of a 200 KB stream measured
0.0802 → 0.0814 ms (+1.5%, 1.2 µs). These small timing differences are consistent
with measurement noise; neither path is claimed to be faster. No material
regression was demonstrated. Large-file memory reductions are the main result.
The request fixture disables HTTP caching; a browser may already coalesce some
wire requests. Sharing the application promise still avoids redundant body
buffers and parsing. Slow clients continue to occupy a handler thread until the
response completes; #110 supplies separate client limits.

### Method and reproduction

Run the scripts from their respective PRs with the matching baselines:

| Script | Baseline | Method |
| --- | --- | --- |
| `flate.py BASE_ROOT HEAD_ROOT` | #111 (`7f5da19`) | 21 paired batches, 100 decodes each; separate allocation samples |
| `chunks.py BASE_ROOT HEAD_ROOT SCRATCH_DIR REAL_PDF` | #122 (`3715a93`) | 21 warm / 7 cold paired calls; byte-for-byte validation; real thesis plus generated large-stream/5,000-object fixtures |
| `streaming.py BASE_ROOT HEAD_ROOT SCRATCH_DIR` | #114 (`3eb0d1f`) | 21 paired calls after warmup; counting sink excludes network/browser |
| `ranges.mjs BASE_ROOT HEAD_ROOT` | #111 (`7f5da19`) | 21 paired batches of 2,000 requests after warmup |
| `chunk_fetch.mjs` | Pre-coalescing loader included in script | 11 paired trials after warmup, controlled loopback server |
| `build_support.py STAGING_BASE_ROOT SCRATCH_DIR` | #106 | 11 staging samples after warmup; five gzip samples per level |

Use `python` for `.py` scripts and `node` for `.mjs`. Arguments are checkout
roots, an existing real PDF where required, and a disposable scratch directory
on the filesystem being evaluated. On the development host, keep scratch on
`/Volumes/Scratch` and Bazel/dependency caches on `/Volumes/Cache`. Scripts are
manual benchmarks, not timing thresholds in CI; tests assert algorithmic/memory
bounds instead of noisy wall-clock expectations.

## Areas deliberately left unchanged

- **Staging:** the safe-copy baseline (#106) took 4.47 ms for ten 1 MiB files and
  9.89 ms for 100 × 2 KiB files. Do not restore hardlinks/symlinks: compiler writes
  must not reach source inputs. Platform-specific copy-on-write paths might help
  very large image sets but need representative workload and filesystem testing
  before adding native syscall/fallback code. No clone implementation is claimed
  to have been benchmarked here.
- **Cache compression:** the real hello snapshot expands to 66,355,200 tar bytes.
  Level 6 took 1,542 ms and produced 17,997,147 bytes; level 1 took 280 ms and
  produced 18,469,892 bytes (+2.63%). Level 9 took 11,893 ms for 17,769,086 bytes.
  Keep level 6: the faster setting affects cold snapshot creation only, increases
  transfer/storage costs and changes checked-in reproducible snapshot bytes.
  An opt-in fast ephemeral-cache policy could be considered separately if cold
  snapshot generation proves frequent. This benchmark isolates compression; it
  does not include walking the cache or tar serialization.
- **Compilation and Biber:** preserve the current single action, persistent
  worker and fast preview cache path. DESIGN.md §4.12 already records the full
  split-Biber prototype: citation-neutral edits improved 14–21%, but citation
  changes regressed 60%. This review did not repeat or reopen that experiment.
- **Cache extraction / snapshots:** do not reuse mutable directories between
  hermetic actions or remove content hashes to shave cold-start work. The
  immutable generation and source-isolation audit fixes take precedence.
- **Rendering:** keep the existing viewport-limited canvases/text layers and
  #96's resource-aware invalidation. Do not reintroduce the rejected dedicated
  OffscreenCanvas worker or skip repainting pages whose resources changed.
- **Watch/status polling:** per-input stat scanning and three git subprocesses
  on a two-second TTL remain potential large-workspace costs. No representative
  bottleneck was established, so no watcher rewrite or branch/SHA parsing change
  is proposed. Large monorepo profiling would be the next step if idle CPU is a
  reported problem.
- **Dependencies/CI:** no new runtime dependency or runner changes. Preserve the
  existing OS/Bazel matrix and consumer-Python tests. Cold dependency downloads
  remain subject to upstream latency; weakening pinning or removing validation
  is not a performance fix.

## Validation

Focused Python tests ran both directly and through Bazel's pinned Python.
The combined local branch ran all 39 JavaScript unit tests and the real Chrome
smoke: initial canvas render, source edit, successful rebuild, changed canvas
pixels, no browser errors, source restored afterward. All 34 Bazel test targets passed on the combined branch using
the repository's local CTAN fixture mirror, as CI does. The first invocation
omitted that fixture setup and failed its synthetic-package test; rerunning
with the CI fixture environment passed the entire suite. GitHub matrix status is
recorded on each PR and may still be running; no merge is part of this review.
