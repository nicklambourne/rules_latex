<p align="center">
  <img src="./assets/logo.svg" alt="rules_latex logo" width="200" />
</p>

# rules_latex

[![CI](https://github.com/nicklambourne/rules_latex/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/nicklambourne/rules_latex/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/nicklambourne/rules_latex?label=release&sort=semver)](https://github.com/nicklambourne/rules_latex/releases)
[![License](https://img.shields.io/github/license/nicklambourne/rules_latex)](./LICENSE)
[![Bazel 8](https://img.shields.io/badge/bazel-8.0-43A047)](./.bazelversion)

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
| Reproducible builds             | Possible, manual                  | `reproducible = True` attr     |
| Live preview                    | None                              | `latex_serve` / `latex_serve_web` |
| In-browser SyncTeX              | None                              | Click PDF → jump to source     |
| Air-gapped CI                   | Yes (vendored TeX Live)           | Yes (`cache = "foo.tar.gz"`)   |
| First-build cost                | Many MB of TeX Live as needed     | ~20 MB tectonic + 10–100 MB cache snapshot per document |

The first time you build, `rules_latex` runs Tectonic once online to
populate a per-document cache (~10–100 MB depending on the document),
then runs the actual compile offline against it. Bazel's action cache
makes the prime a one-time cost; subsequent builds (including across
CI machines via the remote cache) skip it entirely. See the
[implicit cache pipeline](./DESIGN.md#44-network-policy) section
of `DESIGN.md` for the design rationale.

## Quick start

In your `MODULE.bazel`:

```python
bazel_dep(name = "rules_latex", version = "0.2.0")

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
    # reproducible = True       # byte-identical PDF across builds
    # synctex = True            # click PDF → jump to source in serve_web
    # cache = "cv_cache.tar.gz" # for fully air-gapped builds
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
paper, thesis, and beamer slides — and the full [user guide](https://nicklambourne.github.io/rules_latex/).

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
3.17) is staged onto PATH at compile time. On Linux arm64 — where
upstream ships no prebuilt biber — set `biber_strategy = "system"` to
fall back to a distro-installed binary. See
[DESIGN.md §4.9](./DESIGN.md#49-biber).

### Live preview

```bash
bazel run //:cv_live        # opens cv.pdf in your system viewer, auto-rebuilds on save
bazel run //:cv_web         # http://127.0.0.1:8765/ — PDF.js + SSE auto-reload
```

`latex_serve_web` self-hosts PDF.js (no CDN), preserves scroll
position across reloads, and offers click-to-source via SyncTeX when
the underlying document declares `synctex = True`.

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

Three offline modes plus a default implicit pipeline; the rule
chooses based on what you've configured. See
[DESIGN.md §4.4](./DESIGN.md#44-network-policy) for the full
hierarchy.

## Supported platforms

| Platform        | tectonic | biber             | bundle |
|-----------------|---------|-------------------|--------|
| Linux x86_64    | ✅ musl  | ✅ glibc            | ✅      |
| Linux aarch64   | ✅ musl  | ⚠️ system only     | ✅      |
| macOS x86_64    | ✅       | ✅ universal binary | ✅      |
| macOS aarch64   | ✅       | ✅ universal binary | ✅      |
| Windows x86_64  | ✅ MSVC  | ✅                  | ✅      |

The Linux arm64 biber gap is documented in
[DESIGN.md §4.9](./DESIGN.md#49-biber); workarounds available today.

## Project status

| Layer | Status |
|---|---|
| Core rules (`document`, `library`, `pkg`, `test`) | Stable since v0.1.0 |
| Toolchain (`tectonic`, `bundle`, `biber`) | Stable since v0.2.0 |
| Live preview (`serve`, `serve_web`) | Stable since v0.2.0 |
| SyncTeX reverse-sync | Stable since v0.2.0 |
| Implicit cache pipeline | Stable since v0.2.0 |
| Self-hosted PDF.js | Stable since v0.2.0 |
| Modern biblatex (3.18+) | Blocked on upstream bundle refresh ([#1](https://github.com/nicklambourne/rules_latex/issues/1)) |
| Linux arm64 biber | Pending v0.3 (build from source) |
| SyncTeX forward-sync | Future (`DESIGN.md` §5.6) |

## Compatibility

- **Bazel**: 8.0+ (Bzlmod-only)
- **Tectonic**: 0.16.9 (pinned)
- **biber / biblatex**: 2.17 / 3.17 (paired by control-file format)
- **TeX Live**: 2022 (frozen — see [DESIGN.md §4.10](./DESIGN.md#410-biberbiblatex-version-coupling-and-the-upstream-bundle-staleness))

## Documentation

- [User guide](https://nicklambourne.github.io/rules_latex/) — generated from Stardoc, with the Material theme
- [`DESIGN.md`](./DESIGN.md) — architectural rationale, the v0.x → v1.0 roadmap, and open questions
- [`CHANGELOG.md`](./CHANGELOG.md)
- [`examples/`](./examples/) — five runnable examples (letter, CV, paper, thesis, beamer)

## License

Apache License 2.0. See [`LICENSE`](./LICENSE).
