# Changelog

All notable changes to `rules_latex` are documented here. This project follows
[Semantic Versioning](https://semver.org/) once v1.0.0 is reached; before
that, expect breaking changes in any v0.x release.

## [0.4.0] - 2026-05-23

### Added

- **`tectonic.toolchain(modern_biblatex = True)` opt-in.** Makes
  the toolchain extension fetch biblatex 3.21 from CTAN and biber
  2.21 from the rules_latex GitHub mirror, and overlay them on top
  of the bundle via Tectonic's `-Z search-path` flag. Required for
  modern biblatex extension styles (`biblatex-apa` 9.x,
  `biblatex-chicago`, `biblatex-ieee`, `biblatex-nature`, etc.)
  which need biblatex 3.18+ / biber 2.18+ — the bundle pins 3.17 /
  2.17, an incompatibility documented in DESIGN.md §4.10. Default
  workspaces stay on the stable 3.17 / 2.17 pair, which is fine
  for the five core biblatex styles the bundle ships. See
  `docs/site/getting-started/bibliography.md#modern-citation-styles`.

- **CTAN auto-resolve for transitive dependencies.** Listing
  `ctan_packages = ["biblatex-apa"]` (or any other entry) now also
  auto-fetches everything that package transitively requires from
  CTAN that isn't in Tectonic's bundle. The resolver scans each
  fetched package's `.sty` / `.cls` / `.bbx` / `.cbx` / `.lbx` /
  `.dbx` files for `\RequirePackage` / `\usepackage` /
  `\LoadClass`, filters references against a shipped bundle
  manifest (`latex/toolchain/bundle_manifest.txt`, ~6100 entries
  for tlextras-2022.0r0), HEAD-probes CTAN for any name not in the
  manifest, and recurses. Single compile pass; users only list
  entry-point packages.

- **`RULES_LATEX_CTAN_MIRROR` environment variable.** Replaces the
  hardcoded `https://mirrors.ctan.org` URL prefix with a
  configurable one. Three audiences: CI (point at a local fixture
  server to avoid real-CTAN flake), enterprise users behind firewalls
  with internal CTAN mirrors, and reproducibility-conscious users
  pinning a specific mirror.

- **Retry-with-backoff on CTAN downloads.** `_retry_urlretrieve`
  wraps each URL attempt up to three times with 1s/2s/4s
  exponential backoff. Retries `URLError` (timeouts, DNS, TLS) and
  `HTTPError` with 5xx status; 4xx propagates immediately so the
  existing fallback chain takes over. Helps real users on flaky
  networks just as much as it helps CI.

- **Targeted failure-path hints.** When the populate-cache step
  fails, the tool now greps the tectonic `.log` for the missing-
  file LaTeX error AND for the biblatex/biber version-mismatch
  signature. The first emits a hint that names the requiring
  package and suggests adding the missing name to
  `ctan_packages`; the second points at the
  `modern_biblatex = True` opt-in. Both include direct links to
  the relevant docs section.

- **Proactive `ctan_packages` dep map.** The populate step prints
  a per-package summary of upstream `\RequirePackage`-style
  references so users see what each fetched package pulled in, even
  on successful builds.

- **CTAN fixture mirror for hermetic CI.** Checked-in TDS zips at
  `tests/ctan/fixtures/macros/latex/contrib/{,biblatex-contrib/}*.zip`
  served by a local `python3 -m http.server` started by the CI
  workflow. Integration tests in `//tests/ctan:*` now run
  ~4–8× faster than against real CTAN and are flake-free. See
  `tests/ctan/fixtures/README.md` for the refresh procedure.

- **SyncTeX forward-sync.** New `POST /sync/forward` endpoint on
  `latex_serve_web`. Maps a source `(file, line)` tuple to a PDF
  location via the same SyncTeX index that powers reverse-sync;
  broadcasts a JSON `{"type": "jump", ...}` event over the
  existing SSE channel; the browser scrolls the matching page
  into view and flashes a yellow highlight overlay at the box.
  Editors / CLI shims invoke via curl. Five documented response
  shapes (success / unmatched / file unknown / synctex not
  produced / synctex disabled). Includes editor-integration
  snippets for Neovim, VS Code, and Emacs at
  `docs/site/getting-started/live-preview.md#synctex-forward-sync`.

- **`RULES_LATEX_ACTION_SCHEMA` cache-key contribution.** Baked
  into the env of `TectonicPopulateCache` and `TectonicCompile`
  so adding or removing a declared output on those rules
  invalidates pre-existing action-cache entries (Bazel's action
  cache key doesn't include declared outputs; this paper-cuts the
  class of bug we hit once on synctex). A new
  `action_schema_canary_test` analysistest snapshots the
  declared-output set + verifies the env wiring and fails on
  drift, prompting the developer to bump the constant. See
  `latex/private/action_schema.bzl` and DESIGN.md §5 item 10.

- **`ctan_packages` attribute on `latex_document`, `latex_test`, and
  `latex_cache_snapshot`.** Accepts a list of CTAN package names
  (e.g. `["biblatex-apa"]`) and pulls them from `mirrors.ctan.org`
  in TDS format during the implicit cache pipeline's online prime.
  Closes the gap between Tectonic's frozen 2022 bundle and modern
  CTAN: APA / Chicago / IEEE biblatex styles, recent `tcolorbox`
  releases, niche contrib packages, and so on. Zero new targets
  required — just list package names where they're used.

  ```python
  latex_document(
      name = "thesis",
      main = "thesis.tex",
      srcs = ["thesis.tex", "references.bib"],
      ctan_packages = ["biblatex-apa"],
      biber = True,
  )
  ```

  Compatible with the implicit cache pipeline (default) and with
  per-document cache snapshots; explicitly incompatible with
  `tectonic.bundle()` (the bundle path skips the online prime).
  See `docs/site/getting-started/ctan-packages.md` and the
  new `examples/ctan_paper/` for the user-facing treatment, plus
  `DESIGN.md` for the architectural rationale.

- **Structured cache-snapshot tarball format.** Snapshots produced
  with `ctan_packages` non-empty wrap two trees:
  `cache/` (the tectonic bundle cache, what the flat format used to
  hold) and `ctan_pkgs/` (the extracted TDS overlay).
  `tectonic_compile.py` detects the structure at extract time and
  sets `TECTONIC_CACHE_DIR` + `TEXMFHOME` accordingly. Legacy
  flat-format snapshots from older `rules_latex` releases keep
  working unchanged — the format detection is purely structural.

- New `examples/ctan_paper/` example demonstrating an APA-style
  bibliography via `ctan_packages = ["biblatex-apa"]` + `biber = True`.

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

### Fixed

- **Fetched CTAN packages now actually reach tectonic.** The
  original `ctan_packages` plumbing set `TEXMFHOME` on the
  tectonic invocation, which was a no-op: tectonic doesn't honour
  TEXMFHOME (it's a kpathsea concept, and tectonic uses its own
  simpler resolver). Fetched packages were downloaded and
  extracted but tectonic never consumed them — the bundle served
  every request. Discovered while writing the first end-to-end
  test for the auto-resolver. The fix: switch both the populate-
  cache and compile actions to Tectonic's `-Z search-path` flag,
  walking `ctan_pkgs/` for directories holding package files and
  emitting one flag per directory. Tectonic's lookup is now
  cwd → search-path → bundle, exactly as we'd assumed it
  already was. See DESIGN.md §5 item 12.

- **HTTP HEAD support.** `latex_serve_web`'s embedded server now
  honours `HEAD` requests per HTTP/1.1: every GET endpoint
  returns the same status code and headers under HEAD, with an
  empty body. Previously every HEAD request 501'd with
  "Unsupported method ('HEAD')" — a latent bug that didn't
  surface in normal use (PDF.js and the index page only issue
  GETs) but broke `curl -I`, browser prefetch heuristics, link
  checkers, and any future reverse-proxy in front of the serve
  target. Implementation is in
  `latex/private/serve_web.py.tpl`: a `do_HEAD` re-enters
  `do_GET` with a per-request flag set, and an overridden
  `end_headers()` swaps `self.wfile` to a sink so subsequent
  body writes no-op. The SSE handler (`/events`) short-circuits
  on HEAD to avoid leaking listener threads.

- **Stray-backslash bug in generated bash scripts** in
  `latex_test.bzl` and `latex_cache_snapshot.bzl`. The
  `" \\" + " \\".join([...])` pattern emitted a leading lone
  backslash when `ctan_packages` was empty (PRs #20 / #25).
  Switched to the same `" \\\n    ".join([...])` pattern that
  `src_args` and `pkg_file_args` already use — works with both
  empty and non-empty lists, no conditional needed.

### Added

- **Content-addressed PDF chunk transport.** The serve script now
  parses each compiled PDF's cross-reference (xref) table, breaks
  the PDF into per-object content-addressed chunks (SHA-256 of
  bytes), and exposes two new endpoints:

    * ``GET /pdf-manifest`` — JSON manifest of ``{ pdfSize,
      ranges: [{ objectId, start, end, hash }, ...],
      skeletonRanges }``.
    * ``GET /chunk/<hash>`` — raw bytes of one chunk, served
      with ``Cache-Control: public, max-age=31536000, immutable``
      so the browser's HTTP cache pins it indefinitely.

  Client side, a custom ``PDFDataRangeTransport`` subclass
  intercepts PDF.js's byte-range fetches: ranges covered by a
  chunk in the latest manifest are served from a client-side
  hash cache (or fetched once from ``/chunk/<hash>``), while
  skeleton ranges (PDF header, gaps between objects, trailer)
  come from ``/pdf`` via HTTP Range requests. Chunks are
  prefetched in the background after each reload so subsequent
  page renders are wire-free.

  On a one-line edit to ``examples/cv/cv.tex``, 14 of 20 chunks
  (70%) stay unchanged across the rebuild, dropping the reload's
  network volume by ~50% even on this 24 KB document. For
  multi-page documents (theses, books) the savings approach
  100% because edits typically don't shift page-content stream
  offsets for unaffected pages.

  Chunks live under
  ``$BUILD_WORKSPACE_DIRECTORY/.cache/rules_latex/<doc-slug>/chunks/``,
  GC'd 5 minutes after they leave the active manifest so quick
  edit-undo round-trips stay free. Falls back to whole-PDF
  transport (the previous behaviour) on any parse failure —
  cross-reference-stream-stream PDFs, malformed output, etc.

- New tool: ``tools/pdf_chunks.py`` — stdlib-only cross-reference
  stream and classic xref-table parser. ~150 lines plus
  exhaustive unit tests (cross-reference-stream parse, classic
  xref parse, chunk hashing, coverage invariant, error paths,
  hash stability, atomic writes, dedup).

- **Debounce-then-fire watcher.** `latex_serve_web` now coalesces
  bursts of source-file changes into a single build instead of
  firing one build per detected mtime change. A small FSM
  (`_debouncer_step` in `serve_web.py.tpl`) waits
  `debounce_ms = 250` of source-idle before triggering, with a
  hard cap at `debounce_max_ms = 1500` for continuous-typing
  cases. Both are exposed as attributes on `latex_serve_web`;
  set `debounce_ms = 0` to reproduce the legacy
  fire-on-every-poll behaviour.

  Coalesced bursts get one combined log line at fire time. The
  poll interval (`poll_interval_ms = 80`) is unchanged — it
  controls how *fast* we notice a change, while the debouncer
  controls *when* we act on it.

  Practical motivations: editors that write-then-rename (vim,
  neovim) produce two mtime bumps for one save; format-on-save
  hooks write twice; fast autosave produces many mtime bumps
  for a single logical edit. All three now collapse to one
  build.

- **Pre-extracted serve cache directory.** When the persistent
  serve cache is primed (see prior entry below), the snapshot is
  also extracted into a sibling `cache/` directory protected by
  its own atomicity sentinel. The compile action consumes the
  extracted directory directly as `TECTONIC_CACHE_DIR`, skipping
  the ~100-500 ms of gzip-decompression + 300+ file writes per
  warm rebuild on macOS APFS. Hermeticity-equivalent to the
  previous tarball-passing path: tectonic doesn't write to its
  cache directory under `--only-cached` (verified empirically),
  so concurrent compiles can safely share it.

- **Persistent worker for `TectonicCompile`.** The compile action
  now declares `supports-workers = "1"` +
  `requires-worker-protocol = "json"`. Bazel keeps a single
  `python3 tools/tectonic_compile.py --persistent_worker` process
  alive across actions and dispatches each compile as a
  `WorkRequest` over stdin. Eliminates the ~80-150 ms CPython
  cold-start cost per warm rebuild after the first one. The
  worker implementation is stdlib-only (JSON protocol, not
  protobuf) to keep the no-`rules_python`-dep invariant. Users
  can force the legacy path with
  `--strategy=TectonicCompile=local,sandboxed` for debugging.

- **`tectonic_compile.py --cache-dir`.** New flag accepting a
  pre-extracted cache directory; mutually exclusive with
  `--cache-tarball` and `--bundle`. The implicit-pipeline / user
  `cache=` paths continue to pass tarballs through
  `--cache-tarball`; only the serve-cache fast-path uses
  `--cache-dir`.

- **`latex_serve_web` now auto-primes a persistent cache snapshot
  on startup** for documents that take the implicit-pipeline path
  (no `cache=`, no toolchain bundle). The snapshot lives under
  `$BUILD_WORKSPACE_DIRECTORY/.cache/rules_latex/<doc-slug>/` and
  is reused across serve sessions. Body-only edits to the document
  no longer trigger an online re-prime; rebuilds drop from ~30-90 s
  to ~2-3 s. The first start of any serve target still pays the
  one-time prime cost (online, requires network), but subsequent
  starts and edits are offline and fast.

  The snapshot is invalidated automatically when a rebuild fails
  with a missing-resource error (e.g. the user just added a new
  `\usepackage`): the serve script re-primes and retries the build
  once before giving up. The cache directory is added to
  `.gitignore` on first prime to keep it out of users' source
  trees.

  Documents that set `cache = "..."` or run against a toolchain
  bundle skip all of this and behave exactly as before — the
  serve override only fills a gap that previously made
  `latex_serve_web` painfully slow for the zero-config case.

- `examples/cv` ships with a checked-in cache snapshot
  (`cv_cache.tar.gz`) and uses the `cache=` attribute, matching
  the pattern in `examples/hello`. This is independent of the
  serve-time auto-prime above and gives users a fully-offline
  reference of the explicit-snapshot pattern. Refresh with
  `bazel run //cv:cv_cache_snapshot`.

- New private build setting `//latex:_serve_cache_override`. Not
  part of the public API. Set by `latex_serve_web` to point
  `latex_document` at the persistent serve cache; ignored by
  documents that already have `cache=` or a toolchain bundle.

- New provider `LatexDocumentInfo`. Carries the compile-time inputs
  (main file, biber binary, pkg_files overrides, toolchain handle)
  of a `latex_document` so live-preview rules can drive parallel
  tectonic invocations without re-introspecting attributes.

- `LatexInfo` grows an `offline_strategy` field reporting which
  offline-mode strategy the target resolved to (`"user_cache"`,
  `"bundle"`, or `"implicit"`). `latex_library` and `latex_pkg`
  leave it as the empty string. Consumed by `latex_serve_web` to
  decide whether the persistent-cache fast-path is needed.

### Changed

- **`staging.stage_sources` materialises with hardlinks (then
  symlinks, then copy) instead of unconditional copy.** The
  staging tmpdir is per-action and torn down at action end, so
  the "self-contained snapshot" rationale for unconditional copy
  doesn't actually apply. Hardlinks save ~5-50 ms per
  `stage_sources` call depending on source-set size — small per-
  action but compounds across the live-preview hot path. Falls
  back to copy on filesystems / platforms that don't permit
  linking.

- **`TectonicCompile` and `TectonicPopulateCache` actions now run
  via `ctx.actions.run` directly** (with `/usr/bin/env python3`)
  instead of `ctx.actions.run_shell`, except for the
  `biber_strategy = "system"` escape hatch which still needs the
  shell to propagate `PATH`. Saves ~5-15 ms per action by not
  forking `/bin/sh` to immediately `exec python3`.

- `latex_serve_web`'s default `poll_interval_ms` dropped from 250
  to 80 ms, to reduce perceived save-to-preview latency. The
  watcher is still polling (no `watchdog`/inotify dependency), so
  this is the amortised cost of one `stat()` per watched file per
  80 ms — cheap for the document-tree sizes this serves.

- Tectonic's stdout is now captured and forwarded to stderr inside
  the compile action wrapper. In persistent-worker mode our own
  stdout is the worker protocol channel; this prevents tectonic's
  user-facing progress notes (`note: Running TeX ...`) from
  corrupting Bazel's worker responses. In single-shot mode it
  just collapses all tectonic chatter onto one stream — a UX
  improvement.

### Performance summary

Cumulative effect on warm rebuilds:

| Path | Before | After |
|------|--------|-------|
| `latex_serve_web` persistent-cache (implicit pipeline) | ~2.4 s | ~2.0-2.3 s |
| `bazel build` with explicit `cache=` | ~2.6-2.8 s | ~2.3-2.5 s |
| First-prime cost (cold workspace) | 30-90 s | 30-90 s (unchanged) |

The remaining floor is tectonic itself (~600 ms-2 s depending on
document) plus Bazel client/server startup (~150-400 ms). See
`docs/site/about/roadmap.md` for the levers that would push past
that floor.

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
