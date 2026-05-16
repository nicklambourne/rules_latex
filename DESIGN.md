# rules_latex — Design

This document captures the goals, non-goals, key design decisions, and
known open questions for `rules_latex`. It is meant to be read alongside the
[README](./README.md), which focuses on user-facing behaviour.

## 1. Goals

1. **Make Bazel-based LaTeX builds painless.** Users should be able to drop a
   `latex_document(...)` into a `BUILD.bazel` and have it Just Work, without
   enumerating which LaTeX packages their document uses or maintaining patches
   against an underlying ruleset.
2. **Modern Bazel hygiene.** Bzlmod from day one; toolchain-based; platform
   constraints handled via `@platforms`; no legacy WORKSPACE entry point.
3. **Hermeticity.** Pinned, content-addressed Tectonic binaries; an offline
   package bundle path for environments that disallow network at build time.
4. **Composability.** Documents can `dep` on libraries (shared preambles,
   class files) and resource packages (images, `.bib` files) without weird
   workarounds.
5. **Small, legible codebase.** Easy to read end-to-end; easy to contribute
   to; easy to fork if Tectonic ever stops being the right backend.

## 2. Non-goals (for v0.1)

- **Wrapping pdfTeX/XeTeX/LuaTeX directly.** Tectonic is the only backend.
  Multi-engine support is a possible future direction but multiplies the
  toolchain surface area.
- **Building Tectonic from source via `rules_rust`.** The official prebuilt
  binaries are sufficient for the 99% case.
- **Per-document package isolation.** Tectonic resolves packages from a single
  shared bundle; we don't try to virtualise that further.

## 3. Comparison with alternatives

| Aspect                      | `bazel_latex`                              | `rules_latex` (this repo)              |
|-----------------------------|--------------------------------------------|----------------------------------------|
| Backend                     | TeX Live (full distribution)               | Tectonic (XeTeX + custom driver)       |
| Package management          | Explicit Bazel labels per `.sty`           | Implicit, by Tectonic at compile time  |
| Hermeticity                 | Strong (sandboxed TeX Live)                | Strong with offline bundle; opt-in     |
| WORKSPACE / Bzlmod          | Both, leans WORKSPACE                      | Bzlmod-only                            |
| First build cost            | Many MB of TeX Live fetched as needed      | ~20 MB tectonic binary, ~few MB bundle |
| Maintenance burden          | Patches needed against rule internals      | Single dependency: tectonic            |

## 4. Architecture

### 4.1 Public API surface

Loaded from `@rules_latex//latex:defs.bzl`:

- `latex_document(name, main, srcs, deps = [], outfmt = "pdf", reproducible = False, synctex = False, cache = None, tectonic_args = [])`
- `latex_library(name, srcs, deps = [])`
- `latex_pkg(name, srcs)`
- `latex_test(name, main, srcs, deps = [], outfmt = "pdf", cache = None, forbidden_patterns = [], forbidden_patterns_replace = False, required_patterns = [])`
- `latex_cache_snapshot(name, main, srcs, deps = [], output)`
- `latex_serve(name, document, poll_interval_ms = 250, open_pdf = True)`
- `latex_serve_web(name, document, port = 8765, poll_interval_ms = 250)`
- `LatexInfo` provider (for users authoring their own rules)

The toolchain type is exported at `@rules_latex//latex:toolchain_type` for
custom toolchain registrations.

### 4.2 Toolchain model

A single `toolchain_type` (`//latex/toolchain:toolchain_type`) is consumed by
every rule that invokes Tectonic.

A `latex_toolchain` rule packages two attributes:

| Field    | Description                                                           |
|----------|-----------------------------------------------------------------------|
| `tectonic` | The Tectonic executable for the target platform.                    |
| `bundle`   | Optional offline package bundle (`.tar`). If set, the rule passes `--bundle <path>` and Tectonic runs with no network access. |

A `tectonic` module extension (`@rules_latex//latex/toolchain:extensions.bzl`)
materialises one `tectonic_repository` per supported platform and a single
`rules_latex_tectonic_toolchains` "hub" repository that registers a
`toolchain(...)` for each one, gated by `exec_compatible_with` / `target_compatible_with`.

The pinned Tectonic version and per-platform SHA256 hashes live in
[`latex/private/versions.bzl`](./latex/private/versions.bzl).

### 4.3 Action model

`latex_document` produces one output file (the PDF, by default).
Internally it invokes:

    tectonic -X compile \
        --outfmt <pdf|html|xdv|aux> \
        --outdir <bazel-out-dir> \
        [--bundle <bundle.tar> --only-cached] \
        --keep-logs \
        [user-supplied tectonic_args ...] \
        <main.tex>

Sources are gathered transitively from `srcs` plus every `LatexInfo` provider
exposed by `deps`. The bundle, if present, is an action input so it
participates in Bazel's content-based caching. When a bundle is supplied we
also pass `--only-cached`, which causes Tectonic to refuse any network
access.

### 4.4 Network policy

By default, Tectonic fetches its package bundle on first run from
`relay.fullyjustified.net`. This is convenient but non-hermetic and a single
point of failure.

`rules_latex` supports four modes, in priority order:

1. **Per-document checked-in cache snapshot.** A `latex_cache_snapshot`
   target is run once with `bazel run` to compile the document in
   online mode, capture the resulting tectonic cache directory
   (typically 10–100 MB depending on the document), and tar it up
   reproducibly into the source tree. The `latex_document(cache =
   ...)` attribute then consumes that snapshot: the action extracts
   it into `$TECTONIC_CACHE_DIR` and runs with `--only-cached`,
   producing a fully hermetic build that doesn't pull the full
   bundle and doesn't run any online prime. Best for air-gapped
   builds and reproducible distribution. See
   [`latex/private/latex_cache_snapshot.bzl`](./latex/private/latex_cache_snapshot.bzl).
2. **Full bundle.** When `tectonic.bundle()` is declared on the
   `tectonic` module extension, a `tectonic_bundle_repository`
   http-fetches the pinned bundle (`tlextras-2022.0r0.tar`) and feeds
   it into every materialised `latex_toolchain`. Actions run with
   `--bundle <path>` and `--only-cached`, no network access at build
   time. The downside: every first build fetches ~3 GB.
3. **Implicit cache pipeline (default, new in v0.2).** When neither
   (1) nor (2) is set, the `latex_document` rule synthesises a
   two-action pipeline:
   - `TectonicPopulateCache` runs `tectonic` ONCE in online mode
     against the document's sources, captures the resulting cache
     directory as a deterministic `.tar.gz`, and emits it as a
     Bazel-declared output. The action is marked
     `requires-network = "1"` and content-addressed by .tex sources +
     tectonic toolchain version.
   - `TectonicCompile` consumes that tarball as an action input,
     extracts it into `$TECTONIC_CACHE_DIR`, and runs tectonic with
     `--only-cached` — fully hermetic.

   Because PopulateCache is content-addressed, Bazel's action cache
   makes it a one-time cost per (sources × tectonic × bundle URL)
   tuple. Adding a new `\\usepackage` invalidates the cache; CI shares
   warm caches via the remote cache. Subsequent local rebuilds with
   identical sources hit both action caches and complete in under a
   second. **Users don't write any cache target or check anything in
   for this to work.**
4. **Fully online (legacy).** Setting
   `tectonic_args = ["--no-cache-download-only"]` (or similar) on a
   `latex_document` would suppress (3) and let tectonic fetch
   packages itself per-action. Not currently exposed because we have
   no good use case — kept here for completeness.

Mode precedence: explicit `cache =` always wins; otherwise
toolchain-level bundle wins; otherwise the implicit pipeline kicks
in. All three offline modes produce identical PDFs from identical
sources.

See [`latex/private/latex_document.bzl`](./latex/private/latex_document.bzl)
and [`tools/make_cache_snapshot.py`](./tools/make_cache_snapshot.py).

### 4.5 Reproducibility

By default Tectonic embeds the current wall-clock time as the PDF's
creation/modification date, so identical inputs produce non-byte-identical
PDFs. `latex_document(reproducible = True)` flips on both
`SOURCE_DATE_EPOCH=0` and Tectonic's `-Z deterministic-mode`, which together
produce byte-identical output across clean builds. SyncTeX output is broken
by this flag (Tectonic warns about this); leave it off if you care about
SyncTeX.

### 4.6 Sandbox compatibility

Tectonic by default derives its cache directory from `$XDG_CACHE_HOME` /
`$HOME`, both of which are unset under Bazel's Linux sandbox. To avoid
"Read-only file system (os error 30)" on first invocation, each
`TectonicCompile` action runs through a tiny shell wrapper that allocates a
per-action `mktemp -d` scratch dir and exports it as `TECTONIC_CACHE_DIR`.
The wrapper also propagates `LC_ALL=C.UTF-8` (some downstream helpers like
`biber` insist on a UTF-8 locale).

### 4.7 Live preview

Two preview rules ship with v0.1:

* `latex_serve` — a watcher loop that opens the PDF in the system
  viewer and lets the viewer's own auto-reload handle subsequent
  updates. Minimal: ~200 LOC of Python + a shell launcher.
* `latex_serve_web` — a tiny localhost HTTP server with PDF.js for
  in-browser rendering and Server-Sent Events for "reload" pushes.
  Overleaf-style experience without the cloud round-trip.

Both rules are intentionally implemented as thin watchers around
`bazel build`, not separately-driven Tectonic processes. The
justification:

* **Same toolchain, sandbox, and cache as a regular build.** A document
  that builds happily in `bazel build` and CI but breaks in
  `tectonic -X watch` (different binary version, different bundle,
  different env) is a particularly miserable bug to hit. Sharing the
  compile path with `bazel build` eliminates that class of drift.
* **Build graph aware.** When a document depends on a `latex_library`
  whose sources are edited, the watcher sees them via the document's
  `LatexInfo`; no separate input enumeration. Edits to non-watched
  inputs (e.g. the toolchain binary, the bundle, or the cache snapshot)
  still trigger a correct rebuild because Bazel's analysis picks up the
  staleness.
* **Cross-target sharing.** Multiple `latex_document` targets can share
  a `latex_library`; running a preview on one of them doesn't preclude
  editing the shared library and getting consistent rebuilds.

The cost is a couple of hundred milliseconds of Bazel CLI startup
overhead per rebuild, mitigated with `--watchfs` (Bazel uses
inotify/FSEvents for change detection rather than re-stating every file)
and the always-resident Bazel server. For a small document built against
a checked-in cache snapshot, the steady-state rebuild latency in the
example workspace is in the 200–400 ms range — well within "feels live".
The watcher itself is pure-stdlib Python so consumers don't need
`rules_python` or `watchdog`.

`latex_serve_web` vendors PDF.js into the rule set via the
`@rules_latex_pdfjs` repository (materialised by the `pdfjs` module
extension). The browser imports `pdf.mjs` and `pdf.worker.mjs` from
the running server (`/_pdfjs/pdf.mjs`, `/_pdfjs/pdf.worker.mjs`)
instead of from a CDN, so live preview works air-gapped and the PDF.js
version is content-addressed at build time alongside the rest of the
rule set.

### 4.8 SyncTeX

When `latex_document(synctex = True)` is set, tectonic is invoked with
`--synctex` and the resulting `<name>.synctex.gz` is exposed as an
additional output. `latex_serve_web` auto-discovers that file via the
document's `synctex` `OutputGroupInfo` and offers two affordances:

* Browser-side: clicking on the rendered PDF page POSTs the
  (page, x, y) coordinates (in PDF points) to `/sync/reverse`. The
  response is rendered in the footer as `file:line`.
* Server-side: a minimal SyncTeX v1 parser in
  [`serve_web.py.tpl`](./latex/private/serve_web.py.tpl) reads the
  gzipped synctex file, builds an index of (file_id → path) plus a
  flat list of box records, and resolves clicks to the smallest
  enclosing box. Paths in the synctex file are sandbox-absolute (TeX
  sees the execroot path); the handler maps them back to
  workspace-relative paths by matching basenames against the watched
  source list, which is sufficient for typical single-package
  documents.

`reproducible = True` and `synctex = True` are mutually exclusive on
the same `latex_document` — tectonic's deterministic mode disables
SyncTeX output because aux files would otherwise embed absolute paths
that aren't stable across machines.

Forward-sync (editor → PDF) is intentionally not implemented in v0.x;
the natural surface would be a `POST /sync/forward` endpoint that the
editor posts to, with the server pushing a `jump-to-page-N-line-Y`
event over the existing SSE channel. See §5.6 for the discussion.

### 4.9 Biber

Tectonic implements XeTeX in-process but **shells out to `biber` as an
external executable** when a document uses `\addbibresource` /
`\bibliography` via the `biblatex` package. Under Bazel's sandbox the
PATH is scrubbed, so a system-installed biber isn't visible. To keep
biblatex-based documents building hermetically, `rules_latex` ships a
biber toolchain alongside the tectonic toolchain.

The biber binary lives in `@rules_latex_biber_<platform>`,
materialised by the same `tectonic` module extension that wires up
the tectonic binary. The pinned version is fetched from a
**rules_latex-owned GitHub release mirror** (`biber-mirror-v<version>`)
rather than directly from SourceForge, because SourceForge only
serves predictable URLs for the `current` release rather than
version-pinned ones — content-addressed pinning against upstream's
URL scheme would break on every biber bump.

#### Activation modes

`latex_document(biber = ...)` and `latex_cache_snapshot(biber = ...)`
accept a boolean. When True, the action stages the toolchain biber
binary into a `mktemp -d` scratch dir and prepends that dir to PATH so
tectonic's biber subprocess resolves it by basename.

#### Linux arm64 gap

Upstream doesn't ship a prebuilt biber for Linux arm64. The toolchain
extension materialises biber repos only for platforms in
`BIBER_RELEASES` — currently linux/x86_64, macos/x86_64+aarch64
(universal), and windows/x86_64. On linux/aarch64 a document with
`biber = True` fails at analysis time with a pointer to the
workarounds:

1. Cross-compile on linux/x86_64 (e.g. CI runs on a Graviton runner
   but the build happens via a remote executor on x86_64).
2. Install biber via the distro package manager and set
   `biber_strategy = "system"` on affected targets. Less hermetic —
   the build's behaviour depends on which biber version is on
   `$PATH`, which may not match the rest of the pinned toolchain —
   but unblocks Linux arm64 users today.
3. Wait for the v0.3 plan: build biber from source via a `rules_perl`
   integration. This is a multi-day project (Biber has 50+ CPAN
   module deps) and not justified for v0.2.

### 4.10 Biber/biblatex version coupling

Biber and biblatex are **tightly coupled by a "control file format"
version number**. biblatex writes a control file in the format it
knows; biber refuses to process a control file whose format it
doesn't recognise. Each minor biber release maps to a single
acceptable control file version, and biblatex point-releases bump the
format version periodically.

Concretely, the pinned tectonic bundle (`tlextras-2022.0r0`, dated
2022-09-25) ships a build of biblatex that writes control file v3.8.
Biber 2.17 reads v3.8; biber 2.18+ require v3.9 or newer. So
rules_latex must pin biber 2.17 — not the latest 2.21 — until the
bundle is refreshed.

The upstream `tectonic-texlive-bundles` project (which historically
shipped bundle updates) was archived in October 2024 with no
successor, so we're stuck on this pair until either:

* Tectonic upstream resurrects bundle distribution and we follow with
  matching biber, or
* `rules_latex` ships its own bundle (built from a TeX Live source
  tree with `tectonic -X bundle create`) and bumps both biblatex and
  biber together. This is a v1.0 candidate; tracked in §5.8.

## 5. Open questions / future work

These are deliberately out of scope for v0.1 but worth flagging.

1. **Tectonic v2 workspace mode.** Tectonic v2 introduced a project format
   with `Tectonic.toml`. Worth supporting eventually, but the simpler
   `-X compile <main.tex>` invocation is enough for v0.1.
2. **`bibtex` / `makeindex` toolchain attrs.** Tectonic vendors these
   internally, but advanced workflows may want to swap them. Add as
   optional fields on `latex_toolchain` later if there's demand. Biber
   is already done (§4.9).
3. **`latex_lint`.** Wraps `chktex` / `lacheck`. Could ship as an optional
   toolchain.
4. **Bundle updates.** The current pinned bundle is `tlextras-2022.0r0`
   (the version tectonic 0.16.9 itself asks for by default). Upstream
   has not cut a newer tlextras release since 2022 and the
   `tectonic-texlive-bundles` repo was archived in October 2024; we
   should track that repo for any new releases and bump when (if) they
   appear.
5. **Caching of intermediate aux files.** Tectonic is fast and Bazel caches
   the action output, so this is probably never worth doing — but worth
   benchmarking on multi-pass documents (e.g. with biblatex).
6. **Forward-sync (editor → PDF) for SyncTeX.** Currently `latex_serve_web`
   only implements reverse-sync (click on PDF → source location). A
   future feature could expose a `POST /sync/forward` endpoint that the
   editor (or a small `bazel run //:foo_jump -- file.tex:42` CLI shim)
   posts to. The server would parse the synctex file in the opposite
   direction (file_id + line → first matching box) and push a
   `jump-to-page-N-y-Y` event over the existing SSE channel, which the
   browser handles by scrolling the relevant page into view and
   highlighting the location. No new comms primitive needed beyond the
   ones we already have.
7. **WebSocket-based live-reload channel.** `latex_serve_web` currently
   uses Server-Sent Events for the server→browser "reload" signal, which
   is unidirectional. WebSockets would allow the browser to push state
   back (current scroll position, current zoom, "I'm idle, debounce
   builds", typed-ahead source edits, etc.) over the same connection
   and would also handle binary frames more naturally if we ever wanted
   to push PDF deltas instead of triggering a re-fetch. The cost is
   non-trivial: Python's stdlib doesn't ship a WebSocket server, so
   we'd either hand-roll RFC 6455 frame handling (~100–200 lines of
   security-relevant Python) or take a third-party dep that pulls in
   `rules_python`. Neither is justified by the v0.1 feature set: the
   one duplex feature we want (SyncTeX forward-sync from a CLI to the
   browser) is solvable with a `POST /sync/forward` endpoint that
   piggybacks on the existing SSE channel for the resulting jump
   event. Revisit if a future feature genuinely needs duplex binary
   comms.
8. **`rules_latex`-shipped TeX Live bundle.** The pinned upstream
   `tlextras-2022.0r0` bundle and the matched biber 2.17 are both
   ~3 years stale (and effectively unmaintained — see §4.10). The
   long-term fix is for `rules_latex` to build its own bundle from a
   recent TeX Live source tree using `tectonic -X bundle create`,
   host it on the `rules_latex` GitHub releases, and bump biber to
   match. This is a multi-day project (the upstream
   `tectonic-texlive-bundles` builder is a Rust program plus a few
   thousand lines of Perl glue) and not v0.2 material, but it's the
   only path to landing modern biblatex/CTAN.
9. **Biber from source for linux/aarch64.** Upstream ships no
   prebuilt biber for that triple. Building biber from source means
   resolving its 50+ CPAN dependencies via a Bazel-friendly Perl
   ecosystem (most likely `rules_perl` plus a vendored Perl), then
   driving `pp` (the PAR packager) to bundle everything into a
   single executable. Not trivial, but the only fully-hermetic
   answer for that platform. Tracked separately because of the
   significant work involved.

## 6. Versioning

`rules_latex` will follow semver post-1.0. Pre-1.0 releases (v0.x) can break
API freely; expect every rule to potentially change shape.

## 7. Release process

- Tag `vX.Y.Z` on `master`. The `.github/workflows/release.yml` workflow
  runs automatically and:
  - Verifies the tag matches the `version = ...` field in `MODULE.bazel`.
  - Produces `rules_latex-X.Y.Z.tar.gz` via `git archive`.
  - Computes its sha256 and a BCR-formatted `integrity = "sha256-…"` hash.
  - Publishes a GitHub Release with the archive and BCR submission
    snippet in the release notes.
- The Bazel Central Registry PR is opened manually (one-time per
  release) using the snippet from the release notes.

The post-tag bits below are still manual:

- Drafting `CHANGELOG.md` entries before tagging.
- Opening the BCR PR against
  [`bazelbuild/bazel-central-registry`](https://github.com/bazelbuild/bazel-central-registry).
