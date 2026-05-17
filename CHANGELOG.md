# Changelog

All notable changes to `rules_latex` are documented here. This project follows
[Semantic Versioning](https://semver.org/) once v1.0.0 is reached; before
that, expect breaking changes in any v0.x release.

## [Unreleased]

### Added

- `latex_serve_web` now detects when it's being launched from a VS
  Code-family editor's integrated terminal (via `TERM_PROGRAM` =
  `vscode` / `cursor` / `vscodium`) and prints an
  `<editor>://vscode.simpleBrowser/show?url=...` URI alongside the
  plain http URL. Cmd/Ctrl-clicking that URI in the editor's terminal
  opens the live preview as a Simple Browser tab in the same window —
  no separate window or extension required.

- New `open_on_start` attribute on `latex_serve_web` (default
  `False`). When `True`, the preview is opened automatically once the
  server is ready: in a Simple Browser tab via the editor CLI when an
  editor is detected, otherwise in the system default web browser.
  The plain http URL is always printed regardless, so users can copy/
  paste manually if either auto-open path fails.

## [0.3.1] - 2026-05-17

### Fixed

- `latex_test` script generation used `${{...}}` (double braces) for
  shell variable expansions in a section of the launcher that wasn't
  passed through `.format()`. macOS bash silently tolerated the
  malformed form; Linux bash rejected it with "bad substitution",
  breaking `latex_test` targets in CI. Drop the extra braces.

- Buildifier docstring-header lint regressions on
  `_resolved_pkg_files` helpers. Add proper one-line summaries.

- `latex_test(biber_strategy = "system")` silently produced a broken
  test script (`use_system_biber` was set but never wired). Replace
  silent inability with an explicit `fail()` at analysis time:
  `latex_test` doesn't currently support system biber because the
  test sandbox scrubs PATH.

## [0.3.0] - 2026-05-17

### Changed (breaking)

- **Main-rooted source staging.** Both `TectonicPopulateCache` and
  `TectonicCompile` actions now stage sources into a temporary work
  directory and run Tectonic with cwd set to the directory containing
  the main `.tex` file. Relative paths in `main.tex` (in `\input`,
  `\graphicspath`, `\addbibresource`, etc.) resolve against main's
  directory, exactly as they would in an editor-driven local compile.

  Previously, `TectonicCompile` ran tectonic from the Bazel execroot
  with main passed as an execroot-relative path, while
  `TectonicPopulateCache` staged sources under a common-ancestor work
  dir. The two action paths therefore had different cwd conventions
  and could disagree about whether a path resolved.

  **Migration**: documents using `..` in `\graphicspath`,
  `\input{../...}`, or `\addbibresource{../...}` need to update those
  paths. The new layout makes cross-package sources reachable at
  their workspace-relative path (e.g.
  `_shared/logo/logo.png` instead of `../_shared/logo/logo.png`),
  and the new `pkg_files` attribute lets you override placement of
  specific inputs to keep `main.tex` clean.

  See DESIGN.md §4.11 for the full staging contract.

- **`make_cache_snapshot.py` replaced.** The old single-tool design
  is split into:

  - `tools/staging.py`: the shared layout library.
  - `tools/tectonic_populate_cache.py`: TectonicPopulateCache and the
    backing tool for `latex_cache_snapshot`.
  - `tools/tectonic_compile.py`: TectonicCompile action wrapper.

  Out-of-tree consumers that referenced `//tools:make_cache_snapshot.py`
  directly need to migrate to the new layout.

### Added

- **`latex_document.pkg_files` attribute.** Map of label →
  relative-path-under-main's-work-dir. Lets you stage a cross-package
  source (typically a `.bib` file) at any path inside main's work
  directory, including as a sibling of main.tex itself. The classic
  use case is sharing one `references.bib` across multiple documents
  in different packages:

  ```python
  latex_document(
      name = "notes",
      main = "notes/main.tex",
      srcs = [...],
      biber = True,
      pkg_files = {"//lib/refs:refs.bib": "refs.bib"},
  )
  ```

  Then `\addbibresource{refs.bib}` in `notes/main.tex` resolves
  correctly. Without `pkg_files` the file would auto-stage at
  `lib/refs/refs.bib` and need to be addressed by that full path
  from `main.tex` (which is also valid).

- **Same `pkg_files` attribute on `latex_test` and
  `latex_cache_snapshot`.** Stay consistent across all three rules.

### Fixed

- Tectonic's bibliography subprocess (biber) refused paths
  containing `..` with "relative parent paths are not supported for
  the external tool". The new main-rooted staging avoids `..` paths
  entirely, fixing biblatex compiles for documents whose `.bib` lives
  in a sibling package.

- `latex_test`'s `--keep-logs` output and tectonic invocation now go
  through the same `tectonic_compile.py` wrapper as `latex_document`,
  so log-path and staging behaviour is identical between the two
  rules. Previously the test rule used its own inline shell snippet
  with subtly different conventions.

## [0.2.0] - 2026-05-16

### Added
- **Biber toolchain.** A `biber` field on the `latex_toolchain` rule
  points at a platform-specific biber binary fetched from a
  rules_latex-owned GitHub release mirror (`biber-mirror-v2.17`). The
  toolchain is materialised by the same `tectonic` module extension
  that wires up tectonic. Pinned to biber 2.17 to match the biblatex
  v3.8 control-file format shipped in the current `tlextras-2022.0r0`
  bundle (see DESIGN.md §4.10). Available on linux/x86_64,
  macos/x86_64+aarch64 (universal), and windows/x86_64; linux/aarch64
  is gapped (see DESIGN.md §4.9).
- **`latex_document(biber = True)`.** When set, the action stages the
  toolchain biber binary onto PATH so tectonic's biblatex subprocess
  finds it. Optional `biber_strategy = "system"` escape hatch
  propagates `$PATH` for users on linux/aarch64 (or air-gapped builds
  with a pre-installed system biber).
- **Implicit cache pipeline.** `latex_document` now synthesises a
  two-action build for documents without an explicit `cache =` or
  toolchain bundle: `TectonicPopulateCache` does one online prime
  (content-addressed by .tex sources × tectonic × bundle URL) and
  feeds the resulting `tar.gz` into a hermetic `TectonicCompile`. The
  online prime is action-cached so subsequent builds skip it
  entirely. Net effect: users get a cache snapshot for free without
  declaring any new targets or checking anything in. See DESIGN.md
  §4.4.
- **`latex_cache_snapshot(biber = True)`.** Same biber wiring as
  above, for the manual-vendoring path. Snapshots primed without
  biber are missing biblatex-related files and won't satisfy
  `latex_document(biber = True)` consumers.
- `latex_document(synctex = True)` produces a `<name>.synctex.gz` next
  to the PDF, exposed via the `synctex` OutputGroup.
- `latex_serve_web` auto-discovers the synctex output when the document
  was built with `synctex = True` and grows a `POST /sync/reverse`
  endpoint that maps PDF-point (page, x, y) clicks to
  `(source_path, line)` tuples. The browser binds `click` on the
  rendered canvases and shows the resolved source location in a
  footer banner.
- Self-hosted PDF.js: `latex_serve_web` no longer fetches PDF.js from
  cdn.jsdelivr.net. The pinned `pdfjs-dist@5.4.149` tarball is
  fetched at repository-rule time via the new `pdfjs` module
  extension (`@rules_latex_pdfjs`), and served at
  `/_pdfjs/pdf.mjs` + `/_pdfjs/pdf.worker.mjs` from the running
  server. Air-gapped live preview now works out of the box.
- New `thesis_like` example: a minimal biblatex+biber document that
  exercises the implicit-cache pipeline end-to-end.

### Changed
- The `latex_toolchain` rule grew a `biber` attribute. Auto-generated
  toolchain BUILD files include it when a biber binary is available
  for the platform; absent otherwise. Backwards-compatible — existing
  toolchains continue to work, just without biber support.
- `latex_serve_web` no longer accepts a `pdfjs_version` attribute; the
  version is pinned in `//latex/private:pdfjs_versions.bzl` and bumped
  via a normal rules_latex release. To override the URL/SHA, fork the
  pin file or vendor your own `@rules_latex_pdfjs`.

## [0.1.0] - 2026-05-16

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
- `latex_serve_web` rule: Overleaf-style in-browser preview. Stands up a
  localhost HTTP server with PDF.js rendering and Server-Sent Events
  for "reload" pushes on every successful rebuild. Preserves scroll
  position across re-renders. Pure-stdlib Python on the server side;
  PDF.js loaded from a CDN at page-load time.
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
