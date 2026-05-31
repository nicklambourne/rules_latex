<p align="center">
  <img src="./assets/logo.svg" alt="rules_latex logo" width="200" />
</p>

# rules_latex

[![CI](https://github.com/nicklambourne/rules_latex/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/nicklambourne/rules_latex/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/nicklambourne/rules_latex?label=release&sort=semver&filter=v*)](https://github.com/nicklambourne/rules_latex/releases)
[![License](https://img.shields.io/github/license/nicklambourne/rules_latex)](./LICENSE)
[![Bazel 8–9](https://img.shields.io/badge/bazel-8.0%20%E2%80%93%209.1-43A047)](./.bazelversion)

Bazel rules for building LaTeX documents with the
[Tectonic](https://tectonic-typesetting.github.io/) typesetting engine.
Bzlmod-only, toolchain-based, hermetic, with auto-managed package caching
and an Overleaf-style live preview.

```python
load("@rules_latex//latex:defs.bzl", "latex_document")

latex_document(
    name = "cv",
    main = "cv.tex",
    srcs = ["cv.tex"],
)
```

That's it. No package enumeration, no checked-in tarballs, no system
LaTeX install. `bazel build //:cv` works on a fresh machine.

## Why

|                                 | [`bazel_latex`](https://github.com/ProdriveTechnologies/bazel-latex) | **`rules_latex`** (this repo) |
|---------------------------------|-----------------------------------|--------------------------------|
| Backend                         | TeX Live (full distribution)      | Tectonic (XeTeX + bundle)      |
| Package management              | Explicit Bazel labels per `.sty`  | Implicit, by Tectonic at compile time |
| Module system                   | WORKSPACE + Bzlmod                | Bzlmod-only                    |
| Bibliography (`biblatex`/biber) | System install, manual flags      | Vendored biber toolchain       |
| Newer-than-bundle CTAN packages | Manual vendoring                  | `ctan_packages = [...]`        |
| Reproducible builds             | Possible, manual                  | `reproducible = True` attr     |
| First-build cost                | Many MB of TeX Live as needed     | ~20 MB tectonic + 10–100 MB cache snapshot per document |

The first time you build, `rules_latex` runs Tectonic once online to
populate a per-document cache (~10–100 MB depending on the document),
then runs the actual compile offline against it. Bazel's action cache
makes the prime a one-time cost; subsequent builds (including across
CI machines via the remote cache) skip it entirely. See the
[caching](https://nicklambourne.github.io/rules_latex/concepts/caching/)
docs page for the user-facing summary and `DESIGN.md` for the
architectural rationale.

## Quick start

In your `MODULE.bazel`:

```python
bazel_dep(name = "rules_latex", version = "0.5.0")

tectonic = use_extension("@rules_latex//latex/toolchain:extensions.bzl", "tectonic")
tectonic.toolchain()
use_repo(tectonic, "rules_latex_tectonic_toolchains")
register_toolchains("@rules_latex_tectonic_toolchains//:all")
```

In a `BUILD.bazel`:

```python
load(
    "@rules_latex//latex:defs.bzl",
    "latex_document",
    "latex_library",
    "latex_test",
)

latex_library(
    name = "preamble",
    srcs = ["preamble.tex"],
)

latex_document(
    name = "cv",
    main = "cv.tex",
    srcs = ["cv.tex"],
    deps = [":preamble"],
    # biber = True              # for biblatex documents
    # ctan_packages = ["..."]   # for packages not in the bundle
    # reproducible = True       # byte-identical PDF across builds
    # synctex = True            # PDF clicks copy <file>:<line> to clipboard
)

# Catch regressions: fails CI if cv.tex stops compiling cleanly.
latex_test(
    name = "cv_compiles",
    main = "cv.tex",
    srcs = ["cv.tex"],
    deps = [":preamble"],
)
```

```bash
bazel build //:cv            # first build: ~30-90s (online prime + compile)
bazel build //:cv            # subsequent builds: ~1-5s (action-cache hit)
bazel test //:cv_compiles
```

For more, see the [examples](./examples/) directory — letter, CV,
paper, thesis, beamer slides, and a CTAN-overlay paper — and the
full [user guide](https://nicklambourne.github.io/rules_latex/).

## Rules

| Rule | Purpose |
|---|---|
| [`latex_document`](./latex/private/latex_document.bzl) | Compile a `.tex` file (plus its transitive sources) into a PDF (or other tectonic-supported format). |
| [`latex_library`](./latex/private/latex_library.bzl) | Group reusable LaTeX source files (preambles, custom style/class files) that other targets depend on. |
| [`latex_pkg`](./latex/private/latex_pkg.bzl) | Group non-LaTeX resources (images, fonts, `.bib` files) that documents may need. |
| [`latex_test`](./latex/private/latex_test.bzl) | Compile a document under `bazel test` and assert on patterns in the tectonic log file (e.g. fail on `LaTeX Error:`). |
| [`latex_cache_snapshot`](./latex/private/latex_cache_snapshot.bzl) | `bazel run`-able command that captures a small, per-document offline cache snapshot for hermetic builds. |
| [`latex_live`](./latex/private/latex_live.bzl) | `bazel run`-able live-preview loop: watches the document's sources, rebuilds via `bazel build` on every save, and serves the result as a localhost HTTP page rendered with PDF.js — Overleaf-style in-browser preview with auto-refresh, search, outline sidebar, and a build-log drawer. |

All six are loaded from `@rules_latex//latex:defs.bzl`.

## Features

### Live preview

`latex_live` is a `bazel run`-able watcher that rebuilds your
document on every save and pushes the result to a localhost HTTP
page rendered with PDF.js.

```bash
bazel run //:cv_live   # http://127.0.0.1:8765/
```

- **PDF.js, vendored** — no CDN, works on disconnected networks,
  doesn't leak the document to a third-party host.
- **WebSocket push transport** — after each successful rebuild the
  server pushes only the changed PDF chunks (content-addressed by
  SHA-256) to the connected tab in a single duplex burst. SSE
  remains at `/events` as a transparent fallback for clients that
  can't upgrade. Scroll position, zoom level, and current page
  are preserved across reloads, so a 90-page thesis doesn't snap
  back to page 1 on every save.
- **Page navigation, zoom, fit modes, fullscreen, download** — a
  proper PDF-viewer chrome with keyboard shortcuts (PageUp/Down,
  Home/End, +/-/0, w/p, f, g, t, Ctrl+F, …).
- **In-document search** — `Ctrl+F` opens a find bar that
  highlights matches across the document with next/prev nav.
- **Selectable text** — PDF.js text-layer overlay means you can
  select and copy text from the preview natively.
- **Outline sidebar** — when the document has hyperref bookmarks
  (any `\section` / `\subsection` / `\chapter`), a collapsible
  left sidebar shows them as clickable nav, with the current
  section highlighted as you scroll.
- **Build-log drawer** — a collapsible bottom panel exposes the
  latest `bazel build` stdout+stderr. Auto-expands on the first
  failed build of a session so the error is one click (or zero)
  away from where you saw the failure.
- **Status pill** — `✓ 1.42 s · build #5 · 12 s ago` with a live-
  ticking "Xs ago" suffix; footer shows current git branch + dirty
  marker so you know which state you're previewing.
- **Light / dark / auto theme** — full palette driven by CSS
  variables; `auto` follows `prefers-color-scheme`. `t` cycles.
- **SyncTeX source-location lookup** — when the document declares
  `synctex = True`, clicking a glyph in the preview resolves the
  matching `.tex` file + line via the generated `.synctex.gz`
  index and copies `<file>:<line>` to the clipboard. The browser
  can't drive your editor, so the click doesn't *jump* — it
  hands you the location to paste wherever opens files for you
  (vim's `:e`, the VS Code Quick Open prompt, `code -g`, etc.).
- **SyncTeX forward-sync (editor → PDF)** — `POST /sync/forward`
  lets an editor plugin point the preview at a `(file, line)`
  location; the matching glyph scrolls into view and gets a
  brief highlight flash. This is the half of SyncTeX where the
  jump is real, because the editor is the one driving it.
- **DPI-aware rendering** — canvases render at `devicePixelRatio`,
  so the preview is crisp on Retina / 4K displays.
- **Configurable port and debouncer** — `port`, `poll_interval_ms`,
  `debounce_ms`, and `debounce_max_ms` are all rule attributes for
  noisy filesystems or shared dev hosts.

> Earlier releases (v0.4.x and below) also shipped a `latex_serve`
> rule that opened the document in the system PDF viewer. It was
> removed in v0.6.0 because the two viewers most users would
> default to on macOS — Preview and Acrobat — don't reliably
> auto-reload when the PDF changes on disk, leaving users with a
> stale preview and no clear hint why. See the [migration note
> in CHANGELOG](./CHANGELOG.md).

### Bibliography (biblatex / biber)

```python
latex_document(
    name = "paper",
    main = "paper.tex",
    srcs = glob(["paper.tex", "references.bib"]),
    biber = True,
)
```

A vendored biber binary (2.21, matching the bundle's biblatex 3.21) is
staged onto PATH at compile time — including on Linux arm64, which is
now covered by a prebuilt binary. Modern citation styles
(`biblatex-apa`, `biblatex-chicago`, `biblatex-ieee`, …) work out of
the box. If you're on an unsupported platform, set
`biber_strategy = "system"` to fall back to a distro-installed binary.
See the
[bibliography guide](https://nicklambourne.github.io/rules_latex/getting-started/bibliography/).

### CTAN packages outside the bundle

```python
latex_document(
    name = "thesis",
    main = "thesis.tex",
    srcs = ["thesis.tex", "references.bib"],
    ctan_packages = ["biblatex-apa"],   # extension style, not in the bundle
    biber = True,
)
```

`rules_latex` ships a self-hosted TeX Live 2026 bundle, so the core
distribution and the standard biblatex styles are current. The
`ctan_packages` attribute fetches anything outside it — niche contrib
packages, bleeding-edge releases — directly from `mirrors.ctan.org` in
TDS format and folds them into the implicit cache pipeline. No extra
targets, no manual vendoring.

Because the bundle ships biblatex 3.21 + biber 2.21, modern extension
styles (`biblatex-apa` 9.x, `biblatex-chicago`, `biblatex-ieee`, …)
work without any toolchain opt-in — just add the style package to
`ctan_packages`. See the
[bibliography guide](https://nicklambourne.github.io/rules_latex/getting-started/bibliography/#modern-citation-styles)
for the version-coupling discussion.

For most documents you don't need this attribute: the bundle covers
~95% of real-world LaTeX. When a fetched package transitively
requires another package outside the bundle, the populate-cache step
surfaces a targeted hint naming the missing file and which of your existing
`ctan_packages` referenced it — so the next iteration is one
attribute edit away. See the [CTAN packages user
guide](https://nicklambourne.github.io/rules_latex/getting-started/ctan-packages/)
for when to reach for it (and when not to).

### Reproducible PDFs

```python
latex_document(
    name = "cv",
    main = "cv.tex",
    srcs = ["cv.tex"],
    reproducible = True,
)
```

Combines `SOURCE_DATE_EPOCH=0` with Tectonic's `-Z deterministic-mode`
to produce byte-identical output across clean builds. CI verifies this
on every push.

### Hermetic offline builds

For fully air-gapped CI, `latex_cache_snapshot` captures a tiny
per-document cache tarball that you check into the repo and pass
as `cache = "..."` — see the [hermetic builds
guide](https://nicklambourne.github.io/rules_latex/concepts/hermetic-builds/).
Most users won't need this: the default implicit pipeline already
caches the online prime through Bazel's action cache.

## Supported platforms

| Platform        | tectonic | biber             | bundle |
|-----------------|---------|-------------------|--------|
| Linux x86_64    | ✅ musl  | ✅ glibc            | ✅      |
| Linux aarch64   | ✅ musl  | ✅ prebuilt         | ✅      |
| macOS x86_64    | ✅       | ✅ universal binary | ✅      |
| macOS aarch64   | ✅       | ✅ universal binary | ✅      |
| Windows x86_64  | ✅ MSVC  | ✅                  | ✅      |

biber 2.21 is prebuilt for every supported platform, including Linux
arm64. `biber_strategy = "system"` remains as an escape hatch for
unsupported platforms.

## Compatibility

- **Bazel**: 8.0+ (Bzlmod-only). CI tests against 8.0.0, 8.7.0, and 9.1.0 on every push and PR.
- **Tectonic**: 0.16.9 (pinned)
- **biber / biblatex**: 2.21 / 3.21 (paired by control-file format)
- **TeX Live**: 2026 (self-hosted bundle — see the [roadmap](https://nicklambourne.github.io/rules_latex/about/roadmap/))

## Documentation

- [User guide](https://nicklambourne.github.io/rules_latex/) — generated from Stardoc, with the Material theme
- [`DESIGN.md`](./DESIGN.md) — architectural rationale, the v0.x → v1.0 roadmap, and open questions
- [`CHANGELOG.md`](./CHANGELOG.md)
- [`examples/`](./examples/) — five runnable examples (letter, CV, paper, thesis, beamer)

## License

Apache License 2.0. See [`LICENSE`](./LICENSE).
