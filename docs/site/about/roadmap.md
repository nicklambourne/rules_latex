# Roadmap and decisions

The current GitHub issue backlog is closed. Work on `rules_latex` is now
demand-driven: new features should start with a concrete document or workflow
that the existing rules cannot support. The detailed decision record lives in
[`DESIGN.md` §5](https://github.com/nicklambourne/rules_latex/blob/master/DESIGN.md#5-decisions-and-future-work).

## Delivered

| Feature | Status | Issue |
|---|---|---|
| SyncTeX forward lookup (source → PDF) | Shipped in v0.4.2 | [#8][issue-8] |
| Automatic transitive CTAN package resolution | Shipped in v0.4.2 | — |
| Self-hosted TeX Live 2026 bundle with biblatex 3.21 | Shipped in v0.6.0 | [#1][issue-1] |
| Vendored biber 2.21 on Linux arm64 | Shipped in v0.6.0 | [#10][issue-10] |
| Fast-path live rebuilds (`serve_fast`) | Shipped in v0.6.1 | — |
| Bazel Central Registry publication | v0.6.1 is published; later releases use the repository's manually dispatched publish workflow | — |
| Long-document live-preview performance | Completed on `master` after v0.6.1 | [#50][issue-50] |
| Hermetic private Python 3.13 toolchain | Completed on `master` after v0.6.1 | [#2][issue-2] |

## Measured or considered, then declined

| Proposal | Decision | Issue |
|---|---|---|
| Tectonic workspace mode (`Tectonic.toml`) | Not planned: its single-document configuration model does not replace Bazel's declared inputs, outputs, bundles, or toolchains | [#3][issue-3] |
| Swappable `bibtex` / `makeindex` executable attributes | Not planned without a concrete unsupported document; Tectonic's integrated tools cover current users | [#4][issue-4] |
| `latex_lint` wrapping chktex or lacheck | Not planned: another cross-platform toolchain and configuration surface is not justified by current demand | [#5][issue-5] |
| Reusing intermediate TeX state between builds | Measured but declined: no-change builds improved by 0.6–0.8 s, while citation-changing rebuilds regressed by about 2.2 s and the state created correctness risks | [#7][issue-7] |
| Dedicated OffscreenCanvas worker | Measured during the long-document work and deferred: main-thread raster cost was not the dominant bottleneck after viewport gating, bounded queues, and page reuse | [#50][issue-50] |

## Possible future work

The live-preview warm path is already fast, but these ideas remain available if
real workloads expose the corresponding bottleneck.

| Lever | Potential benefit | Why it remains deferred |
|---|---|---|
| **Multiplex persistent workers.** Let one Python worker handle parallel document builds. | 100–400 ms when building several documents concurrently | Requires a re-entrancy audit; single-document builds gain nothing. |
| **Share the persistent serve cache across documents.** | Avoid a 30–90 s first prime for each additional document using the same packages | Needs a multi-document cache ownership and invalidation design. |
| **Share cache state between normal builds and live preview.** | Avoid a 30–90 s prime when switching modes in a fresh workspace | Reading from `bazel-bin` is configuration-dependent; the reverse direction needs a declared-input design. |
| **Ship a common-package cache prelude.** | Reduce typical first prime from 30–90 s to extraction plus a few downloads | Adds a large maintained artefact; current first-prime reports do not justify it. |
| **Key implicit cache population on package directives rather than full sources.** | Avoid re-priming after ordinary prose edits outside live preview | Requires reliable source scanning and changes the action-key model. |
| **Lower cache snapshot compression.** | Save roughly 0.5–1 s while creating a snapshot | Increases stored snapshot size for a cold-path-only gain. |
| **Bind the preview server before a cold prime finishes.** | Show progress immediately instead of waiting for the first build | Improves startup feedback, not total build time. |
| **Use an action environment variable for the live cache override.** | Save roughly 50–200 ms when alternating between build and live-preview commands | The current build-setting wiring is more explicit and hermetic. |

## Deliberately outside the scope

- **Replacing Tectonic with a directly managed TeX Live engine stack.** The
  project now ships a pinned Tectonic-compatible TeX Live bundle, but Tectonic
  remains the engine and package resolver. Replacing it would discard the
  single-binary, content-addressed model on which the rules are built.
- **Wrapping pdfTeX, XeTeX, or LuaTeX directly.** Multiple engines would
  multiply the toolchain and test surface; Tectonic's XeTeX-derived engine
  covers current use cases.
- **Virtualising the bundle per document.** Per-document cache snapshots are
  supported, but the underlying package bundle remains a shared toolchain
  artefact.

[issue-1]: https://github.com/nicklambourne/rules_latex/issues/1
[issue-2]: https://github.com/nicklambourne/rules_latex/issues/2
[issue-3]: https://github.com/nicklambourne/rules_latex/issues/3
[issue-4]: https://github.com/nicklambourne/rules_latex/issues/4
[issue-5]: https://github.com/nicklambourne/rules_latex/issues/5
[issue-7]: https://github.com/nicklambourne/rules_latex/issues/7
[issue-8]: https://github.com/nicklambourne/rules_latex/issues/8
[issue-10]: https://github.com/nicklambourne/rules_latex/issues/10
[issue-50]: https://github.com/nicklambourne/rules_latex/issues/50
