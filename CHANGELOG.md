# Changelog

All notable changes to `rules_latex` are documented here. This project follows
[Semantic Versioning](https://semver.org/) once v1.0.0 is reached; before
that, expect breaking changes in any v0.x release.

## [Unreleased]

### Changed

- **SyncTeX reverse-sync framing: "click to jump" → "click to copy
  source location."** The in-app hint, the README, and the docs
  previously described clicking on a glyph in the preview as
  *jumping to source*. A web page can't drive your editor (vim,
  emacs, VS Code, etc.) to a `(file, line)` location — only the
  user (or a server-invoked CLI for a few editors that happen to
  have one on `PATH`) can. The two paths that *would* make the
  jump real both fail silently for too many editor + setup
  combinations to ship as a default, so v0.6 walks the framing
  back instead: the click resolves the source location, displays
  it in the footer, and copies `<file>:<line>` to the clipboard
  via `navigator.clipboard.writeText` (with a textarea +
  `execCommand("copy")` fallback for hostile environments). The
  footer entry is itself clickable to recopy. Users paste the
  location into whatever opens files for them — vim's `:e`,
  the VS Code Quick Open prompt, `code -g`, etc.

  Forward-sync (editor → PDF) is unaffected — that direction
  *does* jump, because the editor is the one driving it.

  No code or attribute changes for end users — `synctex = True`
  still works the same way; the behaviour and copy text are the
  only difference. See `DESIGN.md` §4.8 for the full rationale.

- **Live-preview renders pages lazily.** `latex_serve_web`
  previously re-rendered *every* PDF page into its own canvas on
  each reload — invisible on a CV, a perceptible per-page stall on
  a long thesis. Pages now get a dimensioned placeholder up front
  and an `IntersectionObserver` rasterizes each canvas only as it
  nears the viewport (cancelling the raster if it scrolls away
  first); the visible page is painted immediately. Text layers
  stay eager so Ctrl+F search is unaffected. No user-facing API
  change. See `DESIGN.md` §5 #13.

- **Live-preview client JS/CSS extracted into testable modules.** The
  ~1500-line browser client that lived inline in `serve_web.py.tpl`
  is now real ES modules under `latex/private/` (`serve_web.js`,
  `serve_web_synctex.js`, `serve_web.css`), served at `/_assets/`
  instead of inlined. This adds a JS unit-test harness under
  `tests/js/` (`node --test` via `sh_test`, no npm deps) — the JS
  analogue of the repo's system-`python3` test convention. No
  user-facing change to `latex_serve_web`. See `DESIGN.md` §5 #11.

- **Unit-tested the live-preview render path.** The lazy-paint state
  machine + render-observer decision and the `ChunkedTransport`
  byte-range planner are factored into pure modules
  (`serve_web_render.js`, `serve_web_chunks.js`) and unit-tested under
  `tests/js/` with `node --test`. Behavior-preserving refactor; the
  `latex_serve_web` rule now globs its client assets via a filegroup so
  new modules need no rule change.

- **Per-page content index in the live-preview manifest (server side of
  option B).** `pdf_chunks.py` now resolves the PDF page tree —
  including the compressed object stream (`/ObjStm`) tectonic emits —
  and `/pdf-manifest` (and the WebSocket manifest push) carry a per-page
  `{contentHash, width, height}`, so a future change can skip
  re-rendering unchanged pages on reload. Reuses the existing chunk
  hashes, so a page's hash changes iff its content stream did.
  Best-effort: an unparseable page tree yields an empty index and the
  client re-renders every visible page. No behavior change yet — the
  client doesn't consume it until the reuse logic lands. See
  `DESIGN.md` §5 #13.

### Removed

- **`latex_serve` rule (system-PDF-viewer live preview).** The
  rule opened the built document in `open` / `xdg-open` /
  `start` and relied on the PDF viewer to detect the file-on-
  disk change and reload itself. That contract eroded:
  - macOS Preview's auto-reload became unreliable after the
    Sonoma sandbox changes and stopped firing dependably by
    Sequoia.
  - Adobe Acrobat never watched the file on macOS and locks it
    on Windows.
  - Users hitting either default viewer would see "saves don't
    appear in the preview" with no in-rule way to diagnose it.

  v0.6 drops the rule rather than ship a viewer-specific
  workaround (AppleScript force-reload, plugin recommendations,
  etc.). `latex_serve_web` (introduced in v0.2 and overhauled in
  v0.5) covers the use case better in every dimension that
  matters — faster reload via WebSocket chunk push, page
  navigation, in-doc search, outline sidebar, build-log drawer,
  light/dark theme, native text selection.

  **Migration:** if your `BUILD.bazel` has
  ```python
  latex_serve(name = "doc_live", document = ":doc")
  ```
  swap to
  ```python
  latex_serve_web(name = "doc_live", document = ":doc")
  ```
  and run `bazel run //:doc_live` as before. The browser tab
  opens automatically and refreshes on save. Users who genuinely
  prefer a native PDF viewer can keep one open against
  `bazel-bin/<pkg>/<doc>.pdf` — every `bazel build` keeps that
  path fresh — but the viewer must support file-watch reload
  (Skim, Sioyek, Zathura, PDF Expert all do; Preview and
  Acrobat don't).

  Files removed: `latex/private/latex_serve.bzl`,
  `latex/private/serve_watcher.py.tpl`. The
  `latex_serve` symbol is no longer exported from
  `@rules_latex//latex:defs.bzl`.

## [0.5.0] - 2026-05-24

The headline of this release is a full overhaul of the
`latex_serve_web` live preview: faster reload transport, a real
PDF viewer chrome (page nav, search, outline, build log), and a
light/dark theme. Server contract is unchanged — `latex_document`,
`latex_test`, and the toolchain layer are identical to v0.4.2.

### Added

- **WebSocket push transport for live-preview reloads.**
  `latex_serve_web` now exposes a `/ws` endpoint that, after each
  successful rebuild, pushes the chunk manifest plus any PDF
  chunks the connected client doesn't already have — in a single
  duplex burst, no client poll needed. Compared to the previous
  SSE-only flow (reload event → `/pdf-manifest` fetch → one
  `/chunk/<hash>` fetch per missing chunk), this saves two
  pull round-trips on the hot path.

  Hand-rolled stdlib WebSocket server at
  [`tools/ws_server.py`](https://github.com/nicklambourne/rules_latex/blob/master/tools/ws_server.py)
  (RFC 6455 — handshake, framing, ping/pong, fragmentation,
  close). No third-party dependency, no `rules_python` adoption
  needed; the `permessage-deflate` and subprotocol corners of
  the spec are skipped deliberately (chunks are already
  FlateDecode'd, no need for subprotocols on a single-peer
  transport).

  SSE remains at `/events` as a transparent fallback for clients
  that can't upgrade (proxies that don't speak `Upgrade`,
  deployments that fail to load `ws_server.py` on the server
  side, etc.). The user-visible UX is unchanged on the SSE path;
  WS just makes rebuild-to-render lower-latency. See the
  [live-preview docs](https://nicklambourne.github.io/rules_latex/getting-started/live-preview/#websocket-push-transport)
  for the wire format and
  [DESIGN.md §5.7](https://github.com/nicklambourne/rules_latex/blob/master/DESIGN.md)
  for the historical context. Resolves
  [#9](https://github.com/nicklambourne/rules_latex/issues/9).

- **Live-preview chrome overhaul.** The header is now a proper
  PDF viewer control bar instead of three zoom buttons:

  - **Page navigation** — `‹ N / M ›` with an editable page input
    (`Enter` to jump). `IntersectionObserver` tracks current page
    on scroll.
  - **Zoom** — real `%` (was lying as `100%`), plus dedicated
    fit-width `↔` and fit-page `▭` buttons that auto-recompute
    on window resize.
  - **Download** — `⤓` link to `/pdf` with a download attr matching
    the document name.
  - **Fullscreen** — `⛶` toggles `requestFullscreen` on the viewer.
  - **Keyboard** — `PageUp`/`PageDown`, `Home`/`End`, `+`/`-`/`0`,
    `w` (fit width), `p` (fit page), `f` (fullscreen), `g` (focus
    page input), `s` (toggle outline), `l` (toggle log), `t`
    (cycle theme), `Ctrl/⌘+F` (find).

- **In-document search.** `Ctrl/⌘+F` (or the `⌕` header button)
  opens a find bar above the viewer. Substring match, case-
  insensitive, highlights matches in the PDF.js text layer with
  the current match in a stronger accent colour and scrolled into
  view. `Enter` / `Shift+Enter` cycle next/prev with wrap-around.

- **Selectable PDF text.** Each rendered page now carries a PDF.js
  `TextLayer` overlay so the user can select-and-copy text in the
  preview — the canvas-only render in v0.4.x had no selectable
  text at all.

- **Outline sidebar.** Documents with hyperref bookmarks (any
  `\section` / `\subsection` / `\chapter` etc.) get a collapsible
  left sidebar with clickable section nav. Auto-shows on first
  render that produces an outline; the toggle button stays hidden
  for documents without sections. Current-section is highlighted
  as you scroll. Show/hide preference persists via `localStorage`.

- **Build-log drawer.** Collapsible bottom drawer that exposes the
  latest `bazel build` stdout+stderr (capped at 64 KiB,
  head-trimmed). Header shows a summary tail (the last non-empty
  line — usually `Build completed successfully` or the actual
  error); expand for the full log in a scrollable `<pre>`. Auto-
  expands on the first failed build of a session unless the user
  has explicitly closed it. Copy-to-clipboard button. New `/log`
  HTTP endpoint + `log-update` WS push event.

- **Build status + git context.** The status pill now shows
  `✓ 1.42 s · build #5 · 12 s ago` with a live-ticking "Xs ago"
  suffix (no extra polling — the ticker only restrings the cached
  status). Footer gains a git badge showing branch + dirty
  marker, with the short HEAD SHA in the tooltip. Server-side
  `BuildState.get_git_info()` shells out to git with a 2-second
  TTL cache so the per-second status poll doesn't spawn three
  subprocesses per tick.

- **Light / dark / auto theme.** Full palette refactor onto CSS
  variables. The new `⊙` button cycles `auto` → `dark` → `light`
  (keyboard `t`); `auto` follows `prefers-color-scheme`. Choice
  persists via `localStorage`. The PDF page surface stays white
  in both themes (flipping it would invert document content).

- **Polish details.** Inline data-URI SVG favicon in the project's
  teal accent, 2px accent stripe under the header to pull the
  same colour into the most-visible chrome edge, redesigned empty
  state with a pulsing teal glyph + helpful hint
  (`prefers-reduced-motion`-aware).

- **Unit tests for `BuildState` helpers.** 40 new test cases
  covering `set_log` truncation contract, `get_git_info` caching
  + non-git fallback, `broadcast_chunks` / `broadcast_event` /
  `broadcast_ws_build_failed` / `broadcast_log_update` fan-out
  ordering and isolation, `_combine_output` stderr-after-stdout
  invariant. See `tests/py/test_build_state_*.py`.

### Changed

- **WS manifest payload uses the `ranges` key** matching the
  existing `/pdf-manifest` JSON shape, so `ChunkedTransport`
  consumes both transports through the same code path.

- **Forward-sync (SyncTeX `POST /sync/forward`) events fan out
  to both SSE and WS clients** so editor-jump UX works the same
  regardless of which transport the connected browser tab chose.

- **`run_bazel_build` signature** gains a fourth element
  (`combined_output`); all internal call sites updated. The
  serve script captures the combined stdout+stderr per build and
  hands it to `BuildState.set_log`, which feeds the new
  `/log` endpoint. No effect on the rule-side action protocol.

- **README release badge** now filters tags to `v*` so the
  shields.io semver sort no longer mistakes the
  `biber-mirror-v2.21` tag for a project release.

### Documentation

- **DESIGN.md §5.7** (WebSocket transport) marked SHIPPED with
  the original deferral rationale preserved as an audit trail.
- **DESIGN.md §5 #11** (rules_python trade-off) picks up a
  sub-section recording the JS test-harness gap from the UI
  overhaul as one of the accumulating triggers that would justify
  revisiting the stdlib-only convention.
- **DESIGN.md §5 #13** (new) tracks live-preview render perf for
  long docs: viewport-gated canvas paint, off-screen swap,
  canvas reuse on unchanged geometry, OffscreenCanvas worker
  rendering. Punted for v0.5.0; see
  [#50](https://github.com/nicklambourne/rules_latex/issues/50).
- **`docs/site/about/design.md`** rewrites the "Why SSE not
  WebSockets?" section as "Why WebSocket *and* Server-Sent
  Events?" — the answer is now both.
- **`docs/site/getting-started/live-preview.md`** adds a wire-
  format table for the WS push transport.

## [0.4.2] - 2026-05-24

### Fixed

- **Release workflow now reliably produces a GitHub release.** Two
  release-pipeline-only fixes folded in:
  1. The reusable workflow's `bazel test` step now boots the CTAN
     fixture HTTP mirror first, mirroring CI. Without it,
     `transitive_resolve_test` 404'd against real CTAN looking for
     the synthetic `test-pkg-a` / `test-pkg-b` fixtures (PR #35).
  2. The final `bazel test` line in `bazel_test_command` ends with a
     backslash so the disk/repository cache flags that the reusable
     workflow appends become continuation args rather than a fresh
     shell command (PR #37).

  No code changes versus the v0.4.0 / v0.4.1 attempts — same feature
  set, working pipeline.

## [0.4.1] - 2026-05-23 [YANKED]

> Tag exists but no GitHub release was produced — the release
> workflow's appended `--disk_cache=...` flag tripped over a missing
> shell line-continuation in `bazel_test_command` (exit code 127 after
> all tests passed). Use 0.4.2 instead.

## [0.4.0] - 2026-05-23 [YANKED]

> Tag exists but no GitHub release was produced — the release
> workflow failed at the `bazel test` step (CTAN fixture mirror was
> not started). Use 0.4.2 instead.

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
