# Changelog

All notable changes to `rules_latex` are documented here. This project follows
[Semantic Versioning](https://semver.org/) once v1.0.0 is reached; before
that, expect breaking changes in any v0.x release.

## [Unreleased]

### Added
- Initial scaffold: `latex_document`, `latex_library`, `latex_pkg` rules.
- Bzlmod module extension that downloads Tectonic 0.16.9 binaries for
  Linux x86_64/aarch64 (both musl, statically linked), macOS x86_64/aarch64,
  and Windows x86_64.
- `tectonic.bundle()` module extension tag that opts into a pinned offline
  package bundle (`tlextras-2022.0r0`, format v33, matching what tectonic
  0.16.9 asks for by default), making compilation fully hermetic.
- `latex_cache_snapshot` rule: a `bazel run`-able command that compiles a
  document once in online mode, captures the resulting tectonic cache, and
  writes a small (~10–100 MB) tarball into the source tree. Combined with
  the new `latex_document(cache = "…tar.gz")` attribute, this enables
  fully-offline, content-addressed builds that are orders of magnitude
  smaller and faster than the full-bundle approach.
- `latex_serve` rule: a `bazel run`-able live-preview loop. Watches the
  document's transitive `LatexInfo` sources, rebuilds via `bazel build`
  on every save, and opens the resulting PDF in the system viewer. Uses
  `--watchfs` and the resident Bazel server so steady-state rebuilds for
  small documents complete in ~200–400 ms.
- `latex_document(reproducible = True)` attribute that combines
  `SOURCE_DATE_EPOCH=0` with `-Z deterministic-mode`, producing byte-identical
  PDFs across clean builds.
- `latex_document` now propagates `LatexInfo` so meta-rules like
  `latex_serve` can discover a document's sources without re-declaring
  them.
- `latex_test` rule: compiles a document under `bazel test` and asserts on
  patterns in the tectonic log (e.g. fails the build on `LaTeX Error:`).
  Supports a `cache = …` attribute for fast offline test execution.
- `LatexInfo` provider for inter-target source propagation.
- Apache 2.0 license.
- Hello-world example workspace under `example/` exercising the public API
  end-to-end (document, reproducible document, cache snapshot,
  offline-mode document, live preview, and test).
- CI workflow building the rules and smoke-testing the example on Linux and
  macOS, plus buildifier linting.
- Tag-triggered release workflow that produces a `git archive` source
  tarball, publishes a GitHub Release, and emits a BCR `source.json` snippet
  ready to paste into a Bazel Central Registry PR.
- Design document and README.
