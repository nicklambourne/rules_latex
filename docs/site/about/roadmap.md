# Roadmap

The full open-questions discussion lives in
[`DESIGN.md` §5](https://github.com/nicklambourne/rules_latex/blob/master/DESIGN.md).
This page is a friendlier summary of what's planned.

## Near-term (v0.3)

| Feature | Status | Issue |
|---------|--------|-------|
| Linux arm64 biber (build from source via `rules_perl`) | Planned | — |
| SyncTeX forward-sync (editor → PDF jump) | Planned | — |
| `latex_lint` rule (wraps chktex / lacheck) | Considered | — |
| BCR publication automation | In progress | — |

## Medium-term (v0.4–v1.0)

| Feature | Status | Issue |
|---------|--------|-------|
| Modern biblatex (3.18+) via own TeX Live bundle | Watching upstream | [#1][issue-1] |
| Tectonic v2 workspace mode (`Tectonic.toml`) | Considered | — |
| WebSocket-based live-reload (vs SSE) | Considered | — |

## Long-term

| Feature | Status |
|---------|--------|
| `rules_latex`-shipped TeX Live distribution | Multi-day project; only justified once we have a concrete user need |
| `biber`/`bibtex`/`makeindex` toolchain attrs for swapping backends | Speculative |
| Reproducibility of PDF output across platforms (font handling) | Speculative |

[issue-1]: https://github.com/nicklambourne/rules_latex/issues/1

## Performance follow-ups

The bulk of the build-time optimisation work landed in v0.3.2.
For the curious / future-self, here are the remaining levers that
were *considered* and *deferred*, with rough magnitudes. None of
them is essential; the live-preview warm rebuild is already
~2-3 s with the current set of optimisations.

| Lever | Win | Why deferred |
|-------|-----|--------------|
| **`serve_fast = True` opt-in.** Bypass `bazel build` entirely for serve-mode rebuilds when only `.tex` source files changed; invoke `tectonic_compile.py` directly via the worker channel. Falls back to `bazel build` when structural files change. | 150-400 ms per warm rebuild (~50% of remaining latency) | Sandbox/CI parity loss: a fast-path rebuild that diverges from `bazel build` produces "works in serve, fails in CI" surprises. Want this opt-in with prominent docs, not the default. |
| **Multiplex persistent workers.** Today's worker is single-request-at-a-time; `supports-multiplex-workers` lets one Python process handle N parallel requests. | 100-400 ms when building multiple documents in parallel | Requires re-entrancy audit of `tectonic_compile.py` (currently safe but not guaranteed; `os.environ` and `sys.stderr` redirection would need scope tightening). |
| **Share the persistent serve cache across documents in the same workspace.** Two docs that pull the same 50 packages each pay a separate ~60 s prime today. Refactor `derive_cache_layout` so multiple documents share a `TECTONIC_CACHE_DIR` and tar per-doc subsets. | 30-90 s once per extra document, on first prime | Single-doc workspaces gain nothing; refactor of `tools/serve_cache.py` for the multi-doc case. |
| **Look-aside between `bazel build`'s implicit-cache action and `serve_cache.py`.** A user who already ran `bazel build //:doc` should not pay the prime cost when they then run `bazel run //:doc_serve` (and vice versa). | 30-90 s once per workspace, when both paths get used | Look-aside is safe (tectonic's content-addressing prevents stale reads), but the read-from-`bazel-bin` direction is brittle (bazel-bin contents are mode-dependent). |
| **Ship a "common LaTeX prelude" prebuilt cache snapshot in the toolchain.** A small (~30 MB) curated cache covering the top ~50 packages — article, amsmath, hyperref, geometry, etc. Most first primes become "extract + a few online fetches" instead of "all online". | 30-90 s → ~5 s for typical first prime | ~500 LOC, maintenance burden, repo bloat. Only worth doing if first-prime cost becomes a top complaint. |
| **Key the implicit-pipeline populate action on the `\usepackage` set, not full sources.** The serve-cache override already sidesteps this for serve mode; this would help `bazel build` outside serve mode for users who don't set `cache=`. | 30-90 s per edit (non-serve) | Architectural change; requires a Starlark-time scan of `.tex` files for `\usepackage` directives, then keying the populate action on that fingerprint. |
| **Drop `pack_cache` compression from level 6 to level 1.** Snapshot grows ~1.5× but pack speed doubles. | 0.5-1 s per prime | Cold-path only; not worth the disk bloat for most users. |
| **Async / "server-first" prime on serve startup.** Currently the HTTP server doesn't bind until the prime is complete (~60 s on cold checkout). Bind first, prime in a background thread, report progress via `/status`. | UX, not wall-clock | Worth doing as a polish pass; not strictly performance. |
| **Collapse the three Python `sh_test`s into one `python3 -m unittest tests.py.test_*`.** Three Python startups → one per `bazel test`. | 200-300 ms per cold `bazel test` run | Cosmetic; tests are cached by Bazel anyway. |
| **Switch `_serve_cache_override` from `string_flag` to `action_env`.** Eliminates the analysis-cache flush when alternating between `bazel build` and `bazel run :serve`. | 50-200 ms on serve↔build transitions | Marginally less hermetic; the existing flag-based wiring is also easier to reason about. |

## What's *not* planned

- **Dropping Tectonic for TeX Live.** This is mode (5) in
  [DESIGN.md §4.10](https://github.com/nicklambourne/rules_latex/blob/master/DESIGN.md#410-biberbiblatex-version-coupling-and-the-upstream-bundle-staleness)
  and would essentially require us to rewrite the toolchain layer.
  We picked Tectonic for its single-binary content-addressed
  story; throwing that away to fix package staleness would be
  backwards.
- **Wrapping pdfTeX / XeTeX / LuaTeX directly.** Multi-engine
  support multiplies the toolchain surface area. Tectonic uses XeTeX
  internally and that covers ~99% of real use cases.
- **Per-document package isolation.** Tectonic resolves packages
  from a single shared bundle; we don't try to virtualise that
  further per document.
