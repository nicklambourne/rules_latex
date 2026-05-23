<p align="center">
  <img src="./assets/logo.svg" alt="rules_latex logo" width="200" />
</p>

# rules_latex

[![CI](https://github.com/nicklambourne/rules_latex/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/nicklambourne/rules_latex/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/nicklambourne/rules_latex?label=release&sort=semver)](https://github.com/nicklambourne/rules_latex/releases)
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
bazel_dep(name = "rules_latex", version = "0.4.1")

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
    # ctan_packages = ["..."]   # for packages not in the 2022 bundle
    # reproducible = True       # byte-identical PDF across builds
    # synctex = True            # click PDF → jump to source in serve_web
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
| [`latex_serve`](./latex/private/latex_serve.bzl) | `bazel run`-able live-preview loop: watches the document's sources, rebuilds via `bazel build` on every save, opens the PDF in the system viewer. |
| [`latex_serve_web`](./latex/private/latex_serve_web.bzl) | Like `latex_serve`, but exposes the preview as a localhost HTTP page rendered with PDF.js — Overleaf-style in-browser preview with auto-refresh on save. |

All seven are loaded from `@rules_latex//latex:defs.bzl`.

## Features

### Live preview

Two flavours, both `bazel run`-able. Each wraps the same file
watcher around your `latex_document` target; the difference is
where the rendered PDF shows up.

```bash
bazel run //:cv_live        # local: opens cv.pdf in your system viewer
bazel run //:cv_web         # browser: http://127.0.0.1:8765/
```

**`latex_serve` (local viewer)** — watches the document's transitive
sources, triggers a `bazel build` on every save, then opens (or
re-opens) the resulting PDF in the OS-default viewer
(Preview.app / Skim / Okular / SumatraPDF / …). Because the rebuild
goes through Bazel's action cache, the edit-to-update loop is in
the 2–3 s range once the cache is warm. Useful when you'd rather
stay in a native PDF viewer — Skim's text-search and annotation
flow, Preview's gesture zoom — than a browser tab.

**`latex_serve_web` (browser preview)** — same watcher, but serves a
self-hosted PDF.js page on `127.0.0.1:<port>`:

- **PDF.js, vendored** — no CDN, works on disconnected networks,
  doesn't leak the document to a third-party host.
- **SSE auto-reload** — the browser tab refreshes the moment the new
  PDF lands. Scroll position, zoom level, and page number are
  preserved across reloads, so a 90-page thesis doesn't snap back
  to page 1 on every save.
- **Click-to-source via SyncTeX** — when the document declares
  `synctex = True`, clicking a glyph in the preview jumps your
  editor to the matching `.tex` line via the generated
  `.synctex.gz` index.
- **VS Code-family terminal detection** — when invoked from
  VS Code / Cursor / Windsurf / VSCodium, the preview opens in
  the editor's built-in Simple Browser by default instead of a
  separate window. Outside those terminals it opens the system
  browser. Tunable via the `open_on_start` attribute.
- **DPI-aware rendering** — canvases render at `devicePixelRatio`,
  so the preview is crisp on Retina / 4K displays.
- **Configurable port and debouncer** — `port`, `poll_interval_ms`,
  `debounce_ms`, and `debounce_max_ms` are all rule attributes for
  noisy filesystems or shared dev hosts.

### Bibliography (biblatex / biber)

```python
latex_document(
    name = "paper",
    main = "paper.tex",
    srcs = glob(["paper.tex", "references.bib"]),
    biber = True,
)
```

A vendored biber binary (pinned to 2.17 to match the bundle's biblatex
3.17) is staged onto PATH at compile time. For modern citation styles
(`biblatex-apa`, `biblatex-chicago`, `biblatex-ieee`, …) that need
biblatex 3.18+, set `tectonic.toolchain(modern_biblatex = True)` in
`MODULE.bazel` — that fetches biblatex 3.21 + biber 2.21 alongside the
toolchain. On Linux arm64 — where upstream ships no prebuilt biber —
set `biber_strategy = "system"` to fall back to a distro-installed
binary. See the
[bibliography guide](https://nicklambourne.github.io/rules_latex/getting-started/bibliography/).

### CTAN packages outside the bundle

```python
latex_document(
    name = "thesis",
    main = "thesis.tex",
    srcs = ["thesis.tex", "references.bib"],
    ctan_packages = ["biblatex-apa"],   # not in the 2022 bundle
    biber = True,
)
```

Tectonic's bundle is frozen at TeX Live 2022 and ships only the five
core biblatex citation styles. The `ctan_packages` attribute fetches
modern packages — APA / Chicago / IEEE citation styles, recent
`tcolorbox` releases, niche contrib packages — directly from
`mirrors.ctan.org` in TDS format and folds them into the implicit
cache pipeline. No extra targets, no manual vendoring, no waiting
for an upstream bundle refresh.

Modern biblatex extension styles (`biblatex-apa` 9.x etc.) need the
toolchain-level `modern_biblatex = True` opt-in too, because the bundle's
pinned biblatex 3.17 / biber 2.17 are older than the style files
require. See the
[bibliography guide](https://nicklambourne.github.io/rules_latex/getting-started/bibliography/#modern-citation-styles)
for the full coupling discussion.

For most documents you don't need this attribute: the bundle covers
~95% of real-world LaTeX. When a fetched package transitively
requires another post-2022 package, the populate-cache step surfaces
a targeted hint naming the missing file and which of your existing
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
| Linux aarch64   | ✅ musl  | ⚠️ system only     | ✅      |
| macOS x86_64    | ✅       | ✅ universal binary | ✅      |
| macOS aarch64   | ✅       | ✅ universal binary | ✅      |
| Windows x86_64  | ✅ MSVC  | ✅                  | ✅      |

The Linux arm64 biber gap is documented in the
[bibliography guide](https://nicklambourne.github.io/rules_latex/getting-started/bibliography/#linux-arm64-workaround);
workarounds available today.

## Compatibility

- **Bazel**: 8.0+ (Bzlmod-only). CI tests against 8.0.0, 8.7.0, and 9.1.0 on every push and PR.
- **Tectonic**: 0.16.9 (pinned)
- **biber / biblatex**: 2.17 / 3.17 by default; 2.21 / 3.21 with `tectonic.toolchain(modern_biblatex = True)` (paired by control-file format)
- **TeX Live**: 2022 (frozen — see the [roadmap](https://nicklambourne.github.io/rules_latex/about/roadmap/))

## Documentation

- [User guide](https://nicklambourne.github.io/rules_latex/) — generated from Stardoc, with the Material theme
- [`DESIGN.md`](./DESIGN.md) — architectural rationale, the v0.x → v1.0 roadmap, and open questions
- [`CHANGELOG.md`](./CHANGELOG.md)
- [`examples/`](./examples/) — five runnable examples (letter, CV, paper, thesis, beamer)

## License

Apache License 2.0. See [`LICENSE`](./LICENSE).
