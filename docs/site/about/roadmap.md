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
