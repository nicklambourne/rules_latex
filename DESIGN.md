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
- `latex_live(name, document, port = 8765, poll_interval_ms = 80, debounce_ms = 250, debounce_max_ms = 1500)`
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
Internally it runs `tools/tectonic_compile.py`, which:

1. Stages all srcs into a per-action work directory using the
   main-rooted layout (see §4.11).
2. Invokes Tectonic with cwd at the work directory and `main` passed
   as a basename:

       tectonic -X compile \
           --outfmt <pdf|html|xdv|aux> \
           --outdir <work> \
           [--bundle <bundle.tar> --only-cached | --only-cached] \
           --keep-logs \
           [user-supplied tectonic_args ...] \
           <main.basename>

3. Copies the produced PDF (and optional `.synctex.gz`) to the
   Bazel-declared output paths.

The same wrapper drives the `TectonicCompile` action in
`latex_document`/`latex_test` and the user-facing
`latex_cache_snapshot` command. `TectonicPopulateCache` uses the
sibling `tools/tectonic_populate_cache.py`, which shares the
staging logic but emits a deterministic cache tarball instead of a
PDF.

Sources are gathered transitively from `srcs` plus every `LatexInfo`
provider exposed by `deps`. The bundle, if present, is an action
input so it participates in Bazel's content-based caching. When a
bundle is supplied we also pass `--only-cached`, which causes
Tectonic to refuse any network access.

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
   http-fetches the pinned bundle (`texlive2026.ttb`, ~1.78 GiB)
   and feeds it into every materialised `latex_toolchain`. Actions run
   with `--bundle <path>` and `--only-cached`, no network access at
   build time. The downside: every first build fetches the whole bundle
   (Bazel's repository cache makes it once-per-machine). The bundle is
   downloaded as a single file (not range-fetched), so the host only
   needs to serve a static object. The **root** module may repoint the
   download at a mirror — see "Self-hosting the bundle" below.
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

   Both the PopulateCache prime and the TectonicCompile replay pass
   `--bundle <DEFAULT_BUNDLE.url>` (the self-hosted TeX Live 2026 `.ttb`
   on R2), not Tectonic's built-in relay. The compile keeps
   `--only-cached`, so it never touches the network — but it **must**
   name the same bundle, because Tectonic namespaces the cached format
   (`latex.fmt`) by bundle identity. If the prime and the replay
   disagree on the bundle, the replay resolves a different digest,
   misses the primed format, and fails with `generating format
   "latex"`. The URL→digest mapping is captured inside the cache
   (`bundles/hashes/`), which is why the replay stays offline. See
   §4.10.

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

#### Self-hosting the bundle (e.g. Cloudflare R2)

`rules_latex` already self-hosts its default bundle (the rebuilt TeX
Live 2026 `.ttb`) on Cloudflare R2 at `rules-latex.ndl.au` — both the
implicit pipeline (mode 3) and the full-bundle path (mode 2) fetch from
there by default, with zero egress cost and no dependency on Tectonic's
CDN. The mechanism below is therefore only for consumers who want to
point at *their own* mirror (e.g. an internal artifact host, or to pin
a different bundle):

```python
# MODULE.bazel (root only; transitive deps can't override this)
tectonic.bundle(
    url = "https://<your-bucket>.r2.dev/texlive2026.ttb",
    sha256 = "e1778ceb8a2f5cc6196d476d076592bc946f3319faf7101fcd957f8580e62b80",
)
```

Mirroring the default bundle keeps the **same sha256** (identical
bytes). Because mode 2 downloads the whole file once (no range
requests), any static object host works; **Cloudflare R2** is the
recommended target — zero egress, durable, and a ~1.78 GiB bundle sits
inside its free storage tier. Setup (one-time, requires a Cloudflare
account — *not* something rules_latex can do for you):

1. **Create a bucket** in the Cloudflare dashboard (R2 → Create bucket),
   e.g. `rules-latex-bundle`.
2. **Expose it publicly** — either enable the bucket's `r2.dev`
   development URL, or (better for production) attach a **custom
   domain** via a Cloudflare-managed DNS zone. Public read is required;
   the bundle is non-secret (it's a curated TeX Live subset).
3. **Upload** the bundle with any S3-compatible client against R2's
   endpoint (`https://<account-id>.r2.cloudflarestorage.com`), using an
   R2 API token (R2 → Manage API Tokens):

   ```bash
   # rclone (configure an R2 remote once, then:)
   curl -fL https://rules-latex.ndl.au/texlive2026.ttb -o bundle.ttb
   sha256sum bundle.ttb            # confirm e1778ceb…e62b80
   rclone copy bundle.ttb r2:rules-latex-bundle/
   # or aws-cli:
   #   aws s3 cp bundle.ttb s3://rules-latex-bundle/ \
   #     --endpoint-url https://<account-id>.r2.cloudflarestorage.com
   ```

4. **Verify** the public URL serves it (`curl -I <public-url>` → `200`),
   then set `tectonic.bundle(url = ..., sha256 = ...)` as above. The
   sha256 pin makes the download tamper-evident regardless of host.

An overridden `url`/`sha256` repoints the mode-2 full-bundle download
only. The implicit pipeline (mode 3) and cache snapshots always prime
against the built-in `DEFAULT_BUNDLE.url`; since the prime and the
offline replay both reference that same URL, they stay digest-consistent
regardless of any mode-2 mirror override.

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

One preview rule ships as of v0.6:

* `latex_live` — a tiny localhost HTTP server with PDF.js for
  in-browser rendering and a WebSocket push transport (SSE
  fallback) for "the manifest changed, here are the new chunks"
  delivery. Overleaf-style experience without the cloud round-trip.
  Comes with page navigation, in-document search, outline sidebar,
  build-log drawer, theme toggle, and SyncTeX two-way sync.

> **Historical note.** Earlier releases (v0.1 → v0.5) also shipped
> a `latex_serve` rule that opened the document in the system PDF
> viewer (`open` on macOS, `xdg-open` on Linux, `start` on
> Windows). The design relied on the viewer to detect the
> file-on-disk change and reload — which used to be true for
> macOS Preview, Linux Evince/Okular, and a handful of other
> viewers. Over time that contract eroded: macOS Preview's
> auto-reload became unreliable after the Sonoma sandbox changes,
> Adobe Acrobat never watched the file in the first place, and
> users hitting either default would see "saves don't appear" and
> have no in-rule way to diagnose it. v0.6 drops the rule rather
> than ship a viewer-specific workaround (AppleScript force-
> reload, plugin recommendations, etc.); the browser preview
> covers the use case better in every dimension that matters
> (faster reload, page navigation, search, theme, no scroll loss
> across rebuilds). Users who genuinely prefer a native viewer
> can still open the rebuilt PDF themselves from any reload-aware
> viewer — Skim, Sioyek, Zathura, PDF Expert all work — pointed
> at `bazel-bin/.../<doc>.pdf`. The rule wasn't doing anything
> for them that they couldn't do with `bazel build` + their own
> tooling.

`latex_live` is intentionally implemented as a thin watcher
around `bazel build`, not a separately-driven Tectonic process.
The justification:

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

#### 4.7.1 Serve-time persistent cache (implicit-pipeline only)

The zero-config workflow — `latex_document(...)` with no `cache=`
attribute and no toolchain bundle — uses the implicit cache
pipeline (§4.4). That pipeline's online-prime action
(`TectonicPopulateCache`) is content-addressed on the full source
set, so any source edit invalidates it and forces a fresh online
prime on every keystroke save. For live preview that turns 2-3 s
compiles into 30-90 s per-edit hangs — unacceptable.

`latex_live` works around this without changing the
implicit-pipeline semantics: on startup it primes a persistent
cache snapshot at
`$BUILD_WORKSPACE_DIRECTORY/.cache/rules_latex/<doc-slug>/cache.tar.gz`,
then passes its absolute path via the private build setting
`--@rules_latex//latex:_serve_cache_override=<path>` on every
`bazel build` it invokes. `latex_document` consults the flag and,
when set, uses the snapshot as its cache source — bypassing the
implicit pipeline entirely. The serve cache lives outside Bazel's
input graph; an `--action_env=LATEX_SERVE_CACHE_NONCE=<mtime>`
flag invalidates the compile action when the snapshot changes.
Documents with `cache=` or a toolchain bundle (already hermetic
and fast) ignore the override.

A few corollaries:

* **First-start cost is unchanged** (one online prime, ~30-90 s).
  Subsequent starts and edits are offline and complete in ~2-3 s.
* **Adding a new `\usepackage`** triggers a missing-resource
  failure inside tectonic; the serve script's regex
  (`tools/serve_cache.py:looks_like_missing_resource`) catches
  this, re-primes the snapshot, and retries the build once. From
  the user's perspective: their edit pauses for ~60 s the first
  time it pulls in a new package, then everything's fast again.
* **Lock-protected** (`flock(2)` on a per-document sidecar file)
  so two simultaneous `bazel run //...:serve` invocations on a
  fresh checkout don't race.
* **`.gitignore` auto-managed**: the first prime appends
  `.cache/rules_latex/` to the workspace's `.gitignore` if not
  already there. Silent on read-only filesystems.
* **Hermeticity trade-off acknowledged**: the snapshot file isn't
  in Bazel's input graph, so a `bazel build` from a fresh
  checkout (without a running serve) still takes the implicit
  pipeline. The override is strictly a live-preview optimisation,
  never engages in CI / batch builds.

See `tools/serve_cache.py` for the cache-management implementation
and `//latex:_serve_cache_override` for the rule-side wiring.

#### 4.7.2 Action-level rebuild optimisations

A handful of further hot-path optimisations apply to *every*
`latex_document` rebuild (serve mode or not):

* **Pre-extracted cache directory** (serve-mode only). When
  `_serve_cache_override` points at a directory (vs a tarball),
  `tectonic_compile.py --cache-dir` skips the per-action gzip
  decompression + 300-file extract into a tmpdir and uses the
  directory as `TECTONIC_CACHE_DIR` directly. Verified safe:
  tectonic does not write back to its cache under `--only-cached`.
  Saves ~100-500 ms per warm rebuild on macOS APFS.

* **Hardlink-or-symlink staging**. `staging.stage_sources`
  materialises staged files via `os.link` (then `os.symlink`,
  then `shutil.copyfile` fallback) instead of unconditional copy.
  The per-action staging tmpdir is torn down at action end so the
  "self-contained snapshot" rationale for copy doesn't apply.
  Saves ~5-50 ms per `stage_sources` call.

* **Direct `ctx.actions.run`**. The `TectonicCompile` and
  `TectonicPopulateCache` actions invoke `/usr/bin/env python3`
  directly rather than going through `/bin/sh -c "exec python3 ..."`.
  The shell wrapper is retained only for the
  `biber_strategy = "system"` escape hatch, which needs in-process
  `PATH` propagation. Saves ~5-15 ms per action.

* **Persistent worker for `TectonicCompile`**. The compile action
  declares `supports-workers = "1"` and uses Bazel's JSON worker
  protocol (`requires-worker-protocol = "json"`). A single
  `python3 tools/tectonic_compile.py --persistent_worker` process
  is kept alive across actions; each compile is dispatched as a
  `WorkRequest` over stdin. Eliminates the ~80-150 ms CPython
  cold-start cost on every warm rebuild after the first.

  The worker is opt-in per-action — Bazel falls back to the
  fresh-process path under
  `--strategy=TectonicCompile=local,sandboxed`. We chose JSON over
  protobuf for the worker protocol to keep the stdlib-only
  invariant; protobuf would require a `rules_python` dep that this
  rule set deliberately avoids.

  One subtle interaction: tectonic's progress notes (e.g.
  `note: Running TeX ...`) are written to stdout by default. In
  worker mode our process's stdout is the protocol channel, so
  the wrapper captures tectonic's stdout via `subprocess.PIPE`
  and forwards it to stderr before responding.

#### 4.7.3 Content-addressed PDF chunk transport

When the server detects that PDF.js can use a chunked transport
(i.e. the PDF parses cleanly as a cross-reference-stream or
classic-xref PDF, which covers everything tectonic produces), it
exposes a manifest of the document's *indirect objects*, each
identified by the SHA-256 of its byte range. The browser maintains
a hash → bytes cache, and on every reload only fetches the chunks
whose hashes it doesn't already have.

Mechanism:

* After each successful build, the watcher thread calls
  `_compute_manifest_post_build`, which delegates to
  `tools/pdf_chunks.compute_manifest`. The chunker parses the
  PDF's xref table (handling both the modern FlateDecoded
  cross-reference *stream* and the classic ASCII xref table),
  enumerates the type-1 uncompressed objects, and writes each
  object's bytes into
  `.cache/rules_latex/<doc-slug>/chunks/<hash>`. Existing chunk
  files with matching size are not rewritten (mtime is kept
  stable for the GC).

* The server exposes:
    - `GET /pdf-manifest` — JSON `{pdfSize, ranges, skeletonRanges}`.
    - `GET /chunk/<hash>` — raw chunk bytes with
      `Cache-Control: public, max-age=31536000, immutable`.
    - `GET /pdf` (with HTTP `Range`) — for skeleton bytes (PDF
      header, gaps between objects, trailer) and as a whole-PDF
      fallback for pre-manifest clients.

* The browser subclasses PDF.js's `PDFDataRangeTransport` to
  intercept the worker's byte-range fetches. For each requested
  range it walks the manifest's chunks: bytes inside a known
  chunk come from the in-memory hash cache (or a one-shot
  `/chunk/<hash>` fetch), bytes outside any chunk come from a
  `/pdf` Range request. A small background prefetcher warms the
  cache after the initial render so subsequent page renders are
  wire-free.

* GC: chunks no longer in the current manifest *and* older than
  five minutes are deleted after each successful build. The
  five-minute floor preserves fast edit-undo round-trips: a
  chunk that vanished from the manifest on edit N is still
  available on edit N+1 if the user reverts within the window.

Falls back to whole-PDF transport (the pre-chunking behaviour)
on any parse failure. The fallback is fully transparent: the
browser detects a 404 on `/pdf-manifest`, calls
`pdfjsLib.getDocument("/pdf?t=...")` directly, and renders the
PDF the old way.

For ``examples/cv/`` (one-page, 24 KB PDF) a single-line edit
keeps 14 of 20 chunks unchanged across the rebuild — about 50%
bandwidth savings on the reload event. For multi-page
documents like a thesis the savings are dramatically higher
because most page-content streams don't shift when a single
page is edited.

See `tools/pdf_chunks.py` for the parser and
`latex/private/serve_web.py.tpl` for the HTTP endpoints and
client-side transport.

`latex_live` vendors PDF.js into the rule set via the
`@rules_latex_pdfjs` repository (materialised by the `pdfjs` module
extension). The browser imports `pdf.mjs` and `pdf.worker.mjs` from
the running server (`/_pdfjs/pdf.mjs`, `/_pdfjs/pdf.worker.mjs`)
instead of from a CDN, so live preview works air-gapped and the PDF.js
version is content-addressed at build time alongside the rest of the
rule set.

#### 4.7.4 `serve_fast`: direct recompile (opt-in)

`latex_live(serve_fast = True)` is an opt-in latency optimisation (off
by default). The warm-rebuild path normally shells out to `bazel
build`; even with `--watchfs`, the resident server, and the persistent
worker (§4.7.2), that still spends ~half the warm latency on Bazel's
CLI + analysis + sandbox setup before the compile runs. `serve_fast`
skips Bazel for content edits and runs the compile directly.

**Mechanism.** Bazel writes a *params file* for every action —
`bazel-bin/<pkg>/<doc>.pdf-0.params`, the exact newline-separated argv
that drove `tectonic_compile.py` (`--tectonic`, `--main`, `--src` …,
`--cache-*`/`--bundle-url`, `--output` …). Those args depend on the
input *set* and the rule attrs, not on the source *bytes*, so they're
stable across content edits. On each fired build the watcher reads that
params file and invokes `tectonic_compile.py` with it directly, from
the Bazel **execution root** (whose symlink forest already points at
the live workspace sources). Two wrinkles: the params are passed as an
explicit arg list (the tool only `@`-expands response files in worker
mode, not for a plain CLI), and the action's `--output`/`--synctex`
files — left read-only by Bazel — are made writable first so the
replay can overwrite them in place (the server reads the PDF from
exactly there; the next real `bazel build` re-materialises them).

**Why it stays correct.** The replay is the *same* `tectonic_compile.py`
invocation Bazel would run, so a successful fast build is byte-for-byte
what `bazel build` produces for the current sources. It runs
un-sandboxed, but `tectonic_compile.py` stages only the declared inputs
into its own work directory and sets its own env (`LC_ALL`, etc.), so
the declared-inputs hermeticity that keeps serve and CI consistent is
preserved — a document that compiles under `serve_fast` but relies on
an undeclared file fails the same way under `bazel build`.

**The fallbacks.** The first build of a session is always a real `bazel
build` (it materialises the params file and primes the serve cache).
After that, a fast build that fails *in a way Bazel could fix* falls
back to `bazel build`; one that fails on a genuine LaTeX error
(undefined control sequence, bad math) is reported as-is rather than
recompiled, since Bazel would fail identically. The discriminator is a
LaTeX **"File `x' not found"** error, which covers the two cases where
the frozen replay args are stale: a missing cached package the serve
cache can re-prime (§4.7.1), and — importantly — **a source the params'
`--src` list doesn't contain yet.** The replay never re-evaluates
`glob()`, so a newly-added file that a glob would now match isn't in
those args; when the document references it, the fast build fails with
"not found", and the fallback `bazel build` re-globs and includes it
(the refreshed params then carry it for subsequent fast builds). So
`serve_fast` matches plain `bazel build` for new globbed sources rather
than getting stuck on them.

**Detecting added/removed sources.** The watcher (fast *and* non-fast)
polls two things: the watched source **files** (mtimes, for content
edits) and the **directories** that contain them (for added/removed
sources). The directory signal is the set of entry names whose suffix
is a source extension — so an in-place edit, or an atomic temp+rename
save (vim's `4913`/`~`, VS Code's `.tmp`), leaves it unchanged and
keeps taking the fast path, while *adding a new `.tex` that a `glob()`
would now match* changes it. A directory change forces a re-globbing
`bazel build` (the frozen replay args can't reflect it), and after any
real build the watcher refreshes its file/dir poll set from the build's
params `--src` list, so newly-globbed sources become content-watched
too. Net effect: dropping a new chapter into a globbed directory
rebuilds and is picked up on its own, with no manual touch or serve
restart — matching what you'd expect from a re-`bazel build`.

**Why opt-in.** It introduces a second compile path that runs outside
Bazel's sandbox and writes into `bazel-out` behind Bazel's back (an
extension of the serve-cache override's acknowledged non-hermeticity,
§4.7.1). For a small, cache-backed document the saving is meaningful
(~50% of warm latency); for a biber-cited document the compile itself
dominates, so it helps less. Default-off keeps the canonical `bazel
build` path the one everyone gets unless they ask for the trade.

### 4.8 SyncTeX

When `latex_document(synctex = True)` is set, tectonic is invoked with
`--synctex` and the resulting `<name>.synctex.gz` is exposed as an
additional output. `latex_live` auto-discovers that file via the
document's `synctex` `OutputGroupInfo` and offers two affordances:

* **Reverse-lookup (PDF → source location).** Clicking on the
  rendered PDF page POSTs the (page, x, y) coordinates (in PDF
  points) to `/sync/reverse`. The server returns the resolved
  `(file, line)`, the browser renders `<file>:<line>` in the
  footer and copies the same string to the clipboard. The footer
  text is itself clickable to recopy if the initial write was
  blocked.

  **Note on framing.** Earlier versions of this section, and the
  in-app hint, called this "click to jump to source." That was a
  lie — a web page can't drive your editor (vim, emacs, VS Code,
  etc.) to a (file, line) location. The two paths that would make
  the "jump" real (server invokes the editor's CLI; or render the
  result as a `vscode://file/...:line` URL-scheme link) both fail
  silently for too many editor + setup combinations to ship as a
  default. v0.6.1 walked the framing back: reverse-sync is a
  *source-location lookup*, with clipboard as the handoff, and
  the user paste it into whatever opens files for them. Forward-
  sync (below) is the half of SyncTeX where the jump *is* real,
  because the editor is the one driving it.

* **Server-side parser.** A minimal SyncTeX v1 parser in
  [`serve_web.py.tpl`](./latex/private/serve_web.py.tpl) reads the
  gzipped synctex file, builds an index of (file_id → path) plus a
  flat list of box records, and resolves clicks to the smallest
  enclosing box. Paths in the synctex file are sandbox-absolute (TeX
  sees the execroot path); the handler maps them back to
  workspace-relative paths by matching basenames against the watched
  source list, which is sufficient for typical single-package
  documents.

* **Forward-sync (editor → PDF).** Shipped in v0.4. The editor
  (or any CLI tool) POSTs `{file, line}` to `/sync/forward`;
  the server resolves it to a (page, x, y) box via the same
  index, broadcasts a `jump` event to every connected SSE +
  WebSocket client, and the browser scrolls the page into view
  with a brief highlight flash. This direction *does* jump
  because the editor is what drove the navigation in the first
  place — the browser is the passive receiver.

`reproducible = True` and `synctex = True` are mutually exclusive on
the same `latex_document` — tectonic's deterministic mode disables
SyncTeX output because aux files would otherwise embed absolute paths
that aren't stable across machines.

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

A single version is pinned: **biber 2.21**, matching the TeX Live 2026
bundle's biblatex 3.21 via the `.bcf` control-file format (v3.11).
The release table is `BIBER_RELEASES` in
`latex/private/biber_versions.bzl`, mirrored at `biber-mirror-v2.21`.

(Before the TL2026 adoption the bundle shipped biblatex 3.17, so the
default was biber 2.17 and an opt-in `modern_biblatex = True` toolchain
flag overlaid CTAN biblatex 3.21 + biber 2.21 via `-Z search-path`.
The rebuilt bundle ships 3.21 natively, so that opt-in, the 2.17 pin,
and the overlay machinery were all retired — see §4.10.)

#### Activation modes

`latex_document(biber = ...)` and `latex_cache_snapshot(biber = ...)`
accept a boolean. When True, the action stages the toolchain biber
binary into a `mktemp -d` scratch dir and prepends that dir to PATH so
tectonic's biber subprocess resolves it by basename.

#### Linux arm64 gap

The biblatex-biber project historically shipped no prebuilt biber for
Linux arm64, but the gap is closed: a prebuilt biber **2.21** binary
from CTAN's `biber-linux-aarch64` package is mirrored to
`biber-mirror-v2.21`, and the toolchain materialises a biber repo for
every supported platform including linux/aarch64. CI verifies it on the
free `ubuntu-24.04-arm` runner by building `//paper:paper` end to end.

(The pre-TL2026 default stack pinned biber 2.17, for which no
off-the-shelf arm64 binary existed — TeX Live 2022's aarch64-linux had
none — so we built 2.17 from source via a one-off CI job. The TL2026
adoption moved the default to biber 2.21, which CTAN ships prebuilt for
arm64, so the from-source 2.17 build was retired.)

Remaining fallbacks for anyone off the mirrored binaries:
`biber_strategy = "system"`, or cross-compile on linux/x86_64.

### 4.10 Biber/biblatex version coupling, and the upstream-bundle staleness

Biber and biblatex are **tightly coupled by a "control file format"
version number**. biblatex writes a `.bcf` control file in the format
it knows; biber refuses to process one whose format it doesn't
recognise. Each minor biber release maps to a single acceptable
control file version, and biblatex point-releases bump the format
version periodically.

Concretely, the pinned tectonic bundle (the self-hosted
`texlive2026.ttb`) ships biblatex 3.21, which writes control file
v3.11. Biber 2.21 reads v3.11, so rules_latex pins biber 2.21 to match
what the bundle ships. The pin and the bundle are bumped together
(`BIBER_VERSION` in `biber_versions.bzl` alongside `DEFAULT_BUNDLE` in
`bundles.bzl`), so the coupling can't drift.

This used to be a much harder problem, because the only available
bundle was frozen at TeX Live 2022. The history below explains why we
ended up rebuilding the bundle ourselves.

#### Why the upstream bundle was so old

There are four links in the "engine → bundle → package → backend"
chain, each of which is part of the problem:

1. **Tectonic ships zero LaTeX packages itself.** When a document
   does `\usepackage{biblatex}`, Tectonic resolves that by fetching
   `biblatex.sty` (and ~50 other files) from an external **bundle** —
   a single tar archive containing a curated subset of TeX Live.
2. **The bundle is published by upstream `tectonic-texlive-bundles`.**
   That repo takes a TeX Live source release, selects ~15% of its
   files, patches some of them, and packs them into a versioned
   bundle. It was **archived on 2024-10-02 with no successor
   announced**. The last release is `tlextras-2021.3r1` (which despite
   the name was rebuilt with a 2022 TeX Live snapshot and is what the
   CDN serves as `tlextras-2022.0r0`).
3. **Biber and biblatex are version-coupled** by the .bcf format
   number, as described above.
4. **The Tectonic binary hardcodes a bundle URL.** Tectonic 0.16.9
   asks for `default_bundle_v33.tar` by default; the "v33" is a
   *bundle-format* version baked into the engine source. The
   `--bundle <path>` flag overrides the URL but the engine still
   expects v33-format contents.

Net effect: the LaTeX ecosystem moved on to biblatex 3.21, tikz
3.1.10, new biblatex-apa/biblatex-ieee releases, etc., but upstream
stopped rebuilding the Tectonic bundle, leaving everyone on Tectonic —
not just rules_latex — running 2022-vintage packages. We made it
visible by writing a Bazel rule set around Tectonic; we didn't cause
it. **rules_latex now ships its own rebuilt bundle (TeX Live 2026) to
close the gap — see the recommendation below.**

#### Solution options (graded)

The five plausible responses, in roughly increasing cost and
durability:

1. **Do nothing; wait for Tectonic upstream.** Tectonic might at any
   time cut a fresh bundle and host it on `relay.fullyjustified.net`,
   or bump the format version. Cost: zero. Risk: indefinite wait,
   could be months or years. The discussion on Tectonic GitHub issues
   is intermittent.
2. **Self-host a "shim overlay bundle"** that takes the upstream
   `tlextras-2022.0r0` as a base and drops in newer versions of
   specific packages (biblatex, tikz, citation-style packages)
   pulled from CTAN. Tectonic doesn't natively stack bundles, but a
   repository rule could extract the upstream bundle, layer newer
   files on top, and re-pack. Cost: ~2-3 days. Risk: version-skew
   bugs — upgrading biblatex without upgrading every package that
   depends on its newer features is brittle.
3. **Drive `tectonic -X bundle create` from a Bazel repository rule.**
   Tectonic's own `bundle create` subcommand reads a TOML spec and
   produces a `.tar`. We'd fetch a recent TeX Live source tarball
   via `http_archive`, run `bundle create` against it, and host the
   resulting bundle on a rules_latex GitHub release. Cost: ~2-4
   days. Risk: ongoing maintenance — we own a bundle.toml that has
   to track TeX Live upstream changes once or twice a year. The
   `bundle create` subcommand is mostly internal-use and may have
   quirks against fresh TeX Live snapshots.
4. **Fork the archived `tectonic-texlive-bundles` builder.** It's a
   ~1500-line Rust program + supporting Perl/Python that
   `bundle create` is itself derived from but with additional
   patching machinery. Same end-state as (3), more upfront cost,
   more control. Cost: ~3-7 days. Risk: same ongoing maintenance
   plus a fork to keep aligned.
5. **Drop Tectonic for TeX Live.** Abandon the whole engine-binary
   model and shell out to a system or vendored TeX Live install.
   Solves the package-staleness problem entirely (TeX Live has a
   well-oiled release cycle and `tlmgr` keeps things current) but
   throws away most of Tectonic's value proposition: single
   statically-linked binary, content-addressed packages, no system
   install required. Cost: high (effectively rewriting the
   toolchain layer). Risk: we'd be reinventing what `bazel_latex`
   does, which is the project we set out to *replace*.
6. **Toolchain-side overlay via `-Z search-path`** (**superseded**).
   Tectonic's `-Z search-path=<dir>` flag adds search directories
   in front of the bundle. An earlier release used this to overlay a
   CTAN-fetched biblatex 3.21 + a vendored biber 2.21 on top of the
   2022 bundle, swapping just the two version-coupled components,
   gated behind an opt-in `tectonic.toolchain(modern_biblatex = True)`.
   It worked, but it owned two extra version pins and left the rest of
   the bundle (hyperref, csquotes, kernel) on 2022-vintage — a future
   hard requirement in biblatex 3.22+ for newer kernel features could
   still have bitten. Retired in favour of option 3.

#### Recommendation

**Implemented:** option 3 — rebuild the bundle and self-host it.
We built a fresh `texlive2026.ttb` from the TeX Live 2026 source via
the (archived) `tectonic-texlive-bundles` builder, and host it on
Cloudflare R2 at `rules-latex.ndl.au` (zero egress, range-addressable
`.ttb` format). `DEFAULT_BUNDLE` in `latex/private/bundles.bzl` points
every mode — implicit pipeline, full bundle, cache snapshots — at it.

This solves the staleness problem at the root rather than patching the
two most-visible symptoms:

* The **whole** distribution is current (biblatex 3.21, biber 2.21,
  tikz, hyperref, kernel, …), so version-coupling can't resurface for
  some *other* package the overlay didn't cover.
* Modern citation styles (`biblatex-apa` 9.x, `biblatex-chicago`,
  `biblatex-ieee`, etc.) work with no toolchain opt-in — the bundle
  ships the matching biblatex.
* The `modern_biblatex` opt-in, the biber 2.17 pin, the from-source
  2.17 arm64 build, and the `-Z search-path` overlay machinery are all
  retired. biber is a single pin (2.21) coupled to the bundle's
  biblatex 3.21.

The cost is ongoing maintenance — we now own a bundle that has to track
TeX Live upstream once or twice a year. The build recipe is the
maintainer-only `.github/workflows/bundle-build.yml` workflow
(`workflow_dispatch`-triggered; it authors a spec from the archived
`tectonic-texlive-bundles` builder, fetches + pins a TeX Live source
release, and packs the `.ttb`). That workflow is `export-ignore`d in
`.gitattributes`, so it is tracked in master for reproducibility but does
*not* ship in the release tarball. Refresh procedure: bump `TL_YEAR` /
`TL_DATE` and run the workflow; upload the artifact to R2; bump
`DEFAULT_BUNDLE` + `BIBER_VERSION`; and regenerate `bundle_manifest.txt`
via `tools/extract_bundle_manifest.py` from the new `.ttb.index.gz`.

Re-evaluate if:

* Tectonic upstream resumes cutting fresh bundles (then drop ours and
  just track theirs — option 1).
* The maintenance burden of owning the bundle outweighs the benefit
  (then reconsider the overlay, option 6, for just the coupled pair).
* The maintenance burden of owning the bundle outweighs the benefit,
  *and* Tectonic upstream has resumed publishing — then drop ours.

Tracked at the open-question level in §5 item #8 below, now marked
resolved by the rebuilt bundle.

### 4.11 Source staging (main-rooted layout)

`latex_document` does not run Tectonic against its source files at
their natural execroot paths. Instead, both `TectonicPopulateCache`
and `TectonicCompile` first **stage** sources into a per-action work
directory, then invoke Tectonic with `cwd` set to that directory.

The staging layout is **main-rooted**: paths inside `main.tex` resolve
as if main were the centre of its own universe.

* `main.tex` lands at `<work>/<main.basename>`.
* A src that is a descendant of main's package directory is staged at
  the same path relative to that package, rooted at the work
  directory. Example: a src at
  `study/honours/thesis/thesis/sections/intro.tex` with main at
  `study/honours/thesis/thesis/main.tex` lands at
  `<work>/sections/intro.tex`.
* A src outside main's package is staged at its workspace-relative
  path. Example: a src at `study/llb/lib/references/refs.bib` with
  main in `study/llb/1700/notes/` lands at
  `<work>/study/llb/lib/references/refs.bib`. The author writes
  `\addbibresource{study/llb/lib/references/refs.bib}` — no `..`
  needed.
* Generated files (bazel-out paths) are normalised: the
  `bazel-out/<config>/bin/` prefix is stripped so generated files
  appear at the same path as a hand-written source from the same
  package would.
* The `pkg_files` attribute on `latex_document` (and on `latex_test`
  and `latex_cache_snapshot`) lets the user override placement for
  any specific input. Common use: stage a sibling-package `.bib` file
  as a direct sibling of `main.tex` so the `\addbibresource` line
  reads `{refs.bib}` instead of `{study/llb/lib/references/refs.bib}`.

#### Why staging at all

The pre-v0.3 design had `TectonicCompile` run Tectonic from the Bazel
execroot with main passed as an execroot-relative path. Two problems:

1. **Inconsistency with `TectonicPopulateCache`.** That action
   already staged sources under a common-ancestor work dir (to be
   able to capture a hermetic cache), so relative paths in main.tex
   resolved differently between the two action paths. A document
   could compile in PopulateCache mode but fail in Compile mode
   (or vice versa) depending on which side of the cache it landed.

2. **Tectonic refuses `..` in paths to external tools.** Specifically,
   `biber` is invoked with the resolved path of every
   `\addbibresource{...}` argument; Tectonic rejects paths
   containing `..` for security reasons. A document with a bib in a
   sibling package therefore could not use the natural
   `\addbibresource{../sibling/refs.bib}` form.

The main-rooted layout fixes both: cwd is always main's directory,
sources are reachable without `..`, and the two action paths agree
about the file layout.

#### Why a Python wrapper

The staging logic plus Tectonic's invocation plus output-file
copy-back ends up being a few hundred lines, and embedding it in a
shell snippet inside Starlark is a recipe for portability bugs (the
old code had macOS / Linux divergences in argument quoting). We
already shell out to `python3` for the cache-snapshot tool; the
incremental cost of also driving the compile through a Python tool
is one tool's worth of code that we mostly inherit from the existing
snapshot script.

The Python tools (`tools/tectonic_populate_cache.py`,
`tools/tectonic_compile.py`) share `tools/staging.py`. None of them
take a `rules_python` dependency; they're invoked via
`python3 <tool>` from a shell wrapper, matching the
"stdlib-only Python as a system utility" pattern used elsewhere in
the repo (see tracking issue #2).

#### Trade-off vs `bazel_latex`

`bazel_latex` requires every `\usepackage{foo}` to be listed as an
explicit Bazel target dep against `@bazel_latex//packages:foo` and
sets up its execution so that all dep files appear in cwd. That's
a different way of solving the same path-resolution problem.

The rules_latex approach (staging + main-rooted layout) trades that
explicitness for ergonomic source files: an author can write a
document as they would for any LaTeX editor, with relative paths
anchored at main's directory, and rules_latex makes those paths
work without per-document manual wiring. The cost is that
cross-package files end up at slightly verbose default paths
(`study/llb/lib/references/refs.bib`); the `pkg_files` attribute
mitigates that for the cases where it matters.

## 5. Open questions / future work

These are deliberately out of scope for v0.1 but worth flagging.

1. **Tectonic v2 workspace mode.** Tectonic v2 introduced a project format
   with `Tectonic.toml`. Worth supporting eventually, but the simpler
   `-X compile <main.tex>` invocation is enough for v0.1. Tracked in
   [GitHub issue #3](https://github.com/nicklambourne/rules_latex/issues/3).
2. **`bibtex` / `makeindex` toolchain attrs.** Tectonic vendors these
   internally, but advanced workflows may want to swap them. Add as
   optional fields on `latex_toolchain` later if there's demand. Biber
   is already done (§4.9). Tracked in
   [GitHub issue #4](https://github.com/nicklambourne/rules_latex/issues/4).
3. **`latex_lint`.** Wraps `chktex` / `lacheck`. Could ship as an optional
   toolchain. Tracked in
   [GitHub issue #5](https://github.com/nicklambourne/rules_latex/issues/5).
4. **Bundle updates.** The pinned bundle is now a self-hosted
   `texlive2026.ttb` (TeX Live 2026), since upstream stopped cutting
   tlextras releases and archived `tectonic-texlive-bundles` in October
   2024 (§4.10). We own the refresh cadence: re-run the maintainer-only
   `.github/workflows/bundle-build.yml` (`export-ignore`d, so tracked in
   master but not shipped) to rebuild the `.ttb`, upload it to R2, bump
   `DEFAULT_BUNDLE` (`bundles.bzl`) + `BIBER_VERSION`
   (`biber_versions.bzl`), and regenerate `bundle_manifest.txt` from the
   new `.ttb.index.gz`. If Tectonic upstream resumes publishing bundles,
   reconsider tracking theirs instead. (Tracking issue #6 closed once the
   self-hosted bundle landed.)
5. **Caching of intermediate aux files.** Tectonic is fast and Bazel caches
   the action output, so this is probably never worth doing — but worth
   benchmarking on multi-pass documents (e.g. with biblatex). Tracked in
   [GitHub issue #7](https://github.com/nicklambourne/rules_latex/issues/7).
6. **Forward-sync (editor → PDF) for SyncTeX.** **Shipped in v0.4.**
   `latex_live` exposes a `POST /sync/forward` endpoint that
   maps `(file, line)` → first matching SyncTeX box → broadcasts a
   `{"type": "jump", "page": N, "x": X, "y": Y, "w": W, "h": H}`
   event over the existing SSE channel. The browser scrolls the
   page into view and flashes a yellow highlight overlay at the
   box for ~1.5s. Implementation followed the design sketch
   above; no new comms primitives needed. See
   [docs/site/getting-started/live-preview.md](https://github.com/nicklambourne/rules_latex/blob/master/docs/site/getting-started/live-preview.md#synctex-forward-sync)
   for editor-integration examples (Neovim / VS Code / Emacs).
   Tracked in
   [GitHub issue #8](https://github.com/nicklambourne/rules_latex/issues/8)
   — to close when the issue is updated.
7. **WebSocket push transport for live-reload. SHIPPED.**
   Originally deferred (the section below is the original
   rationale, preserved for context). What changed: even with
   PDF chunking the SSE flow still cost two pull round-trips per
   rebuild — a `/pdf-manifest` fetch followed by one
   `/chunk/<hash>` per missing chunk. WebSocket lets the server
   push the manifest plus the missing chunk bytes in a single
   duplex burst, saving both round-trips on the hot path. We
   hand-rolled the RFC 6455 slice we needed in
   [`tools/ws_server.py`](tools/ws_server.py) (~430 LOC incl.
   docstrings, stdlib-only — see issue #2) to avoid the
   third-party-dep cost; the implementation skips
   `permessage-deflate` (chunks are already FlateDecode'd) and
   subprotocols. SSE remains at `/events` as a transparent
   fallback for clients that can't upgrade (proxies that don't
   speak `Upgrade`, deployments that fail to load the WS module,
   etc.). User-facing docs:
   [docs/site/getting-started/live-preview.md#websocket-push-transport](docs/site/getting-started/live-preview.md#websocket-push-transport).
   Tracked in
   [GitHub issue #9](https://github.com/nicklambourne/rules_latex/issues/9)
   — to close when the issue is updated.

   *Original (now-superseded) deferral rationale, kept for the
   audit trail:* `latex_live` originally used Server-Sent
   Events only. The cost of WebSockets was non-trivial (~100-200
   LOC of security-relevant Python for RFC 6455 framing, or a
   third-party dep + `rules_python`) and the only duplex feature
   on the early roadmap (SyncTeX forward-sync) was solvable with
   a `POST /sync/forward` endpoint over the existing SSE channel.
   The threshold for moving was "we'd actually save round-trips
   on the hot path." Server-pushed PDF deltas hit that bar.
8. **Modern biblatex / fresh TeX Live bundle.** **Resolved.** §4.10
   originally listed five graded options; v0.4 shipped a sixth (a
   toolchain-side overlay via `-Z search-path`, opt-in as
   `tectonic.toolchain(modern_biblatex = True)`) that covered only the
   biblatex+biber slice. We then did the durable fix — option 3:
   rebuilt the whole bundle from TeX Live 2026 and self-host it on R2
   (`rules-latex.ndl.au`). The entire distribution is now current, so
   modern citation styles work with no opt-in, and the overlay / the
   `modern_biblatex` flag / the biber 2.17 pin were all retired.
   Tracked in
   [GitHub issue #1](https://github.com/nicklambourne/rules_latex/issues/1).
9. **Biber on linux/aarch64.** **Resolved.** biber **2.21** (matching
   the TL2026 bundle's biblatex 3.21) is covered on linux/aarch64 by a
   prebuilt binary from CTAN's `biber-linux-aarch64` package, mirrored
   (`biber-mirror-v2.21`), pinned by SHA in `biber_versions.bzl`, and
   CI-verified on the `ubuntu-24.04-arm` runner by building
   `//paper:paper`. `biber = True` documents build hermetically on
   arm64. (The pre-TL2026 default — biber 2.17 — had no off-the-shelf
   arm64 binary and was built from source via a one-off CI job; the
   TL2026 adoption retired that build, since 2.21 ships prebuilt.)
   Tracked in
   [GitHub issue #10](https://github.com/nicklambourne/rules_latex/issues/10).
10. **Rule-version env var to prevent declared-output cache
    poisoning.** **Shipped in v0.4.** The
    `RULES_LATEX_ACTION_SCHEMA` constant in
    `latex/private/action_schema.bzl` is now baked into the env of
    `TectonicPopulateCache` and `TectonicCompile`; bumping it
    invalidates any pre-existing action-cache entries that were
    keyed against an older output schema. The
    `action_schema_canary_test` analysistest snapshots the
    declared-output set of a canonical `latex_document` config
    and fails on drift, prompting the developer to bump the
    constant alongside the output-set change. Tracked in
    [GitHub issue #11](https://github.com/nicklambourne/rules_latex/issues/11).
11. **Python toolchain hermeticity (sh_test vs rules_python).**
    Tests use system `python3` via `sh_test` rather than `py_test`
    + `rules_python`, matching how the runtime tooling is invoked.
    Honest trade-off documented in
    [GitHub issue #2](https://github.com/nicklambourne/rules_latex/issues/2)
    along with the triggers that would justify revisiting.

    **Triggers accumulating in favour of revisiting:**

    - *Frontend (JS / CSS) coverage gap.* **Being addressed.** The
      UI overhaul series (UI PRs 1–7) shipped ~1500 lines of
      browser-side code with no automated tests, because the JS
      lived inline in `serve_web.py.tpl` (nothing importable) and a
      harness seemed to require either a Bazel-managed Node toolchain
      (`rules_nodejs`) or Playwright-via-Python (`rules_python`).
      Resolved without either: the client JS/CSS is now extracted
      into ES modules under `latex/private/` (`serve_web.js`,
      `serve_web_synctex.js`, `serve_web.css`), served at `/_assets/`
      and inlined nowhere, and pure-logic modules are unit-tested
      under `tests/js/` with node's built-in runner (`node --test`)
      wrapped in an `sh_test` — exactly mirroring how the Python
      tests run on the system `python3`, with no npm deps, no
      `node_modules`, and no Bazel JS ruleset. Coverage so far: the
      SyncTeX coordinate math, the lazy-paint render state machine +
      observer decision (`serve_web_render.js`), and the
      ChunkedTransport byte-range planner (`serve_web_chunks.js`). The
      Python-side `BuildState` tests under
      `tests/py/test_build_state_*.py` still cover the server
      contract; DOM/PDF.js-coupled client code is exercised by the
      live-preview smoke test in CI.

    - *`OffscreenCanvas` rendering* (issue #50) would push the
      JS surface area further — render workers, message-passing
      protocols, fallback paths. Hard to ship without browser
      tests without burning a lot of debugging time on UI bugs
      that a `expect(canvas).toRenderPage(3)` check would have
      caught.

    None of these have flipped the decision yet (the
    stdlib-only convention is still load-bearing for the
    "single-binary, content-addressed serve script" story in
    §4.7), but each one is recorded so the threshold is visible.

12. **Automatic transitive CTAN dep resolution.** As of v0.4
    (`ctan_packages`), users list each post-2022 CTAN package
    explicitly. When a package transitively requires *another*
    post-2022 package the populate step fails with a targeted
    hint; the user adds the missing name and rebuilds. Iterate.

    For most documents this terminates in zero or one round-trips —
    the 2022 bundle covers ~95% of TeX Live, so a fetched package's
    deps almost always resolve from the bundle. But for some
    package families (active biblatex citation styles, recent
    `tcolorbox` releases, multi-package contribs) the closure
    chains 2-3 levels deep, and the iterative "rebuild, read hint,
    edit `ctan_packages`, rebuild" cycle gets tedious.

    **Design (shipped via the resolver in
    `tools/tectonic_populate_cache.py`):** auto-resolve the
    transitive closure by default, with no user-facing knob. Goal:
    the default case stays as simple as today — list only the
    entry-point packages in `ctan_packages`, the resolver follows
    the chain.

    > **Historical caveat (now resolved):** when the bundle was
    > frozen at TeX Live 2022 (biblatex 3.17 / biber 2.17), biblatex
    > extension styles (`biblatex-apa`, `biblatex-chicago`, etc.)
    > needed an explicit `tectonic.toolchain(modern_biblatex = True)`
    > opt-in that overlaid CTAN biblatex 3.21 + biber 2.21 — the
    > fetched style files reached tectonic via `-Z search-path`, but
    > the bundle's 3.17 couldn't process them. The TL2026 bundle ships
    > biblatex 3.21 + biber 2.21 natively, so these styles now work
    > with no opt-in; the overlay was retired (§4.10).

    **Algorithm:**

    1. Download each user-listed package (existing behaviour).
    2. Scan each downloaded package's extract tree for
       `\RequirePackage` / `\usepackage` / `\LoadClass` references
       (existing scanner from the failure-hint work).
    3. For each referenced name that is **not** in the static
       bundle manifest and **not** already fetched: HEAD-probe
       CTAN to check whether the name exists as a CTAN package.
       - HTTP 200 → fetch it, add to the queue, scan its tree.
       - HTTP 404 on all fallback URLs → silently skip. This
         filters out TeX-internal names (`expl3`, `l3kernel`
         components), typos, and other false-positive references.
    4. Repeat until the queue is empty.
    5. Run tectonic exactly once against the resolved closure.

    **Why no `ctan_lockfile` attribute.** The Bazel action cache
    already gives us per-build stability: same `ctan_packages` list
    → same action key → same cached output. The resolver runs once
    when the action cache is cold; afterwards every build reuses
    the cached closure. The action cache *is* the implicit
    lockfile — no extra surface area on the rule.

    **Hermeticity trade-off.** Across cold caches (fresh CI
    runner, `bazel clean`, different machine), the resolved
    closure depends on CTAN's state at that moment. If
    `biblatex-apa` upstream adds a new dep between two cold-cache
    builds, the closure differs. For 95% of users this is invisible
    (warm cache, same machine). For users who need cross-machine
    or archival reproducibility, the existing
    `latex_cache_snapshot` is the answer: it captures the resolved
    closure as a checked-in tarball and downstream builds see a
    frozen result. Auto-resolution just means the snapshot fully
    describes the build instead of requiring the user to manually
    transcribe transitive deps into `ctan_packages` first.

    **Why a bundle manifest is required.** The scanner heuristic
    over-reports — `apa.bbx` does `\RequirePackage{biblatex}` even
    though `biblatex` is in the bundle. Without filtering, we'd
    re-fetch bundle-resident packages from CTAN and shadow the
    bundle's versions via the `-Z search-path` overlay. That's
    catastrophic for the biblatex/biber version coupling (§4.10):
    fetching a CTAN biblatex newer than the bundle's 3.21 over the top
    would break biber 2.21 control-file compatibility. The manifest
    must therefore track the bundle (regenerated from the bundle's
    `.ttb.index.gz` whenever `DEFAULT_BUNDLE` changes) — it is
    load-bearing for correctness, not just performance.

    **How the overlay reaches tectonic.** Tectonic's `\usepackage`
    resolver does not honour `TEXMFHOME` (that's a kpathsea concept;
    tectonic uses its own simpler lookup). Fetched packages reach
    tectonic via one `-Z search-path=<dir>` flag per directory under
    `ctan_pkgs/` that holds package files. The flag is flat (no
    recursive descent), which is why we walk and enumerate. An
    earlier implementation set `TEXMFHOME` and the integration
    appeared to work — but it was a no-op; the bundle was the
    actual source of files for those compiles. The `-Z search-path`
    plumbing fixes that.

    Manifest source: generated from TeX Live 2022's `tlpdb` once
    (one-shot Python tool), committed at
    `latex/toolchain/bundle_manifest.txt`. Refresh procedure when
    the bundle bumps (open question #4): re-run the generator
    against the new TL release, commit. The file is a sorted list
    of package names and `.sty`/`.cls` basenames (~80 KB).

    **Why not strategy D (iterative compile-fail-fetch).**
    Serialised compiles. A 3-level chain pays 3 × 30-90s = up to
    270s. The scan-driven approach resolves the same closure with
    one compile and a handful of HEAD requests.

    **Why not strategy C (CTAN JSON catalogue lookup).** New
    runtime dependency on the CTAN JSON API. Outages and rate
    limits become new failure modes. The scanner already gives us
    the same dep information without a third-party API.

    **Risks worth flagging:**

    - **Bundle-manifest drift.** When the bundle bumps, the
      manifest can drift. Mitigation: the generator script is
      checked in; refreshing is a `bazel run :extract_bundle_manifest`
      style one-shot. Worst case if we forget: a build over-fetches
      a few packages, which (because they're bundle-resident
      anyway) usually still compiles fine — the shadowing risk is
      narrow.
    - **No version pinning across cold caches.** Documented above;
      users who care reach for `latex_cache_snapshot`.
    - **Privacy/policy posture.** Auto-resolution makes silent
      network calls beyond what the user explicitly listed. For
      audit-conscious environments this is a regression in
      explicitness. The existing `RULES_LATEX_CTAN_MIRROR` env var
      lets such users point at a controlled mirror; the
      `latex_cache_snapshot` flow lets them ship a frozen,
      auditable closure.

    **What we explicitly do not do:**

    - **No `ctan_lockfile` attribute.** The action cache plus
      `latex_cache_snapshot` cover the same ground without
      introducing a new file format and a new generator target.
    - **No silent acceptance of failure to find a package.** If
      the compile still fails after auto-resolution, the existing
      targeted hint kicks in (the failure-path code from PR #22).

    **Status:** shipped end-to-end in v0.4.

    The resolver, the bundle manifest, the failure-hint chain, the
    HEAD-probe filtering, the proactive dep summary, the
    `RULES_LATEX_CTAN_MIRROR` env override, retry+backoff, and the
    CI fixture server all live in `tools/tectonic_populate_cache.py`
    + `tools/tectonic_compile.py` + the rule plumbing. The modern
    biblatex/biber opt-in (item #8 above) closed the last open
    caveat — the biblatex 3.21 / biber 2.21 overlay reaches
    tectonic via `-Z search-path` as designed.

13. **Live-preview page rendering performance.** **Resolved with a
    dedicated render worker deferred by measurement.**
    `renderAllPages()` historically rendered *every*
    PDF page into its own canvas on each reload, regardless of
    whether the page was in the viewport. On a short doc (CV, hello
    example) that was invisible; on a 50-page thesis the user
    perceived a per-page render cost on every save.

    **Why it was bearable:** the WS chunk-push transport (item
    #7 above) keeps the bytes-on-the-wire cost minimal — only
    changed PDF chunks transit — so the bottleneck is canvas
    paint, not network. And the page-wraps are dimensioned from
    the viewport before paint, so scroll-position survives a
    rebuild.

    **Shipped:**

    - **IntersectionObserver-gated canvas paint.** Each page now
      gets a dimensioned placeholder up front; an
      `IntersectionObserver` (`_attachRenderObserver`) rasterizes a
      page's canvas (`paintPage`) only as it nears the viewport,
      and cancels the in-flight `RenderTask` if it scrolls away.
      The visible page is queued at the highest priority, and at
      most two page rasters run concurrently. Empty text overlays
      commit with the page geometry; the same observer hydrates text
      only for nearby pages. Ctrl+F search hydrates missing layers
      with two concurrent tasks, reports indexing progress, then
      searches the completed layers in document order. The bulk of
      the win for long docs.

    - **Generation-safe atomic swap.** `renderAllPages` collects
      changed and reusable page nodes without moving the live DOM,
      then commits them synchronously once every awaited layout step
      has completed. A monotonically increasing generation prevents
      slower document loads, zooms, text layers, outlines, and canvas
      paints from overwriting newer output. Superseded PDF.js loading
      tasks and documents are cancelled or destroyed after hand-off.

    - **Reuse unchanged pages across reloads (option B).** The
      manifest carries a per-page `{contentHash, width, height}`
      (`PageInfo`), computed server-side by walking the PDF page tree
      — including the compressed object stream (`/ObjStm`) tectonic
      emits — and reusing the chunk hashes, so a page's hash changes
      iff its content stream did (`pdf_chunks.py`). On reload the
      client (`planPageReconciliation`) diffs the page index by
      position and moves the unchanged `.page-wrap`s over instead of
      rebuilding them, keeping their painted canvases; a zoom (scale
      change) or any parse failure falls back to a full re-render. So
      after an edit, only the changed page(s) re-rasterize.
      Index-based, so page insertions/removals re-render the shifted
      tail.

    - **Viewport-bounded canvas memory.** Page placeholders keep only
      CSS dimensions; their intrinsic HiDPI backing stores are allocated
      when the render observer admits them and reset to zero once they
      leave its retention margin. Layout and SyncTeX coordinate mapping
      use the stored PDF.js viewport, so neither depends on a retained
      bitmap. This bounds canvas memory to the visible/nearby working set
      even after scrolling through a long document.

    - **Cheap jank mitigations + measurement.** `content-visibility:
      auto` on `.page-wrap` lets the browser skip paint/compositing of
      off-screen pages; the render observer defers a page's raster
      until scrolling settles (`RENDER_SETTLE_MS`) so a fast fling
      doesn't start-then-cancel a render for every page flung past.
      `window.__serveWebRenderStats.current` now scopes commit,
      first-paint, first-text, text-layer/search indexing, paint-queue,
      reuse, canvas-memory, eviction, and browser Long Task metrics to
      the latest render generation, while the existing raster aggregate
      remains available across the session.

    **Deferred:**

    - **Web worker rendering (gated on measurement).** Move the
      canvas rasterisation off the main thread — a dedicated worker
      running PDF.js against `OffscreenCanvas`es transferred from the
      main thread. Avoids main-thread jank during a heavy paint but
      doesn't reduce total work, and carries real cost: a second
      PDF.js instance, a message protocol, per-page canvas transfer,
      and fallback paths for browsers without OffscreenCanvas
      transfer. It's also the piece most in need of a browser-test
      harness (§5 #11) — invisible to `node --test`. With lazy paint +
      page reuse + the mitigations above already shipped, pursue this
      only if `__serveWebRenderStats` shows real single-page jank on
      representative documents.

    These changes do not alter the network or correctness story. They
    reduce latency and jank as documents get longer. Tracked in
    [GitHub issue #50](https://github.com/nicklambourne/rules_latex/issues/50).

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
