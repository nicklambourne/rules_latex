# rules_latex

Bazel rules for building LaTeX documents with the
[Tectonic](https://tectonic-typesetting.github.io/) typesetting engine.

> Status: **pre-alpha**. APIs may change without notice until v0.1.0 is tagged.

## Why?

Existing Bazel LaTeX rule sets (notably
[`bazel_latex`](https://github.com/ProdriveTechnologies/bazel-latex)) wrap a
traditional TeX Live distribution and require users to declare every required
LaTeX package as an explicit Bazel target. This is hermetic but verbose and
brittle.

`rules_latex` takes a different approach:

- The toolchain is just a single statically-linked
  [Tectonic](https://tectonic-typesetting.github.io/) binary, downloaded as a
  versioned, content-addressed artifact.
- LaTeX packages are resolved by Tectonic itself from an offline bundle, so
  consumers don't have to enumerate every `\usepackage{...}` in their
  `BUILD.bazel`.
- The rules follow modern Bazel conventions: Bzlmod-only, toolchain-based,
  one binary per `(os, cpu)` platform.

## Quick start

In your `MODULE.bazel`:

```python
bazel_dep(name = "rules_latex", version = "0.1.0")

tectonic = use_extension("@rules_latex//latex/toolchain:extensions.bzl", "tectonic")
tectonic.toolchain()
# Opt into the pinned offline package bundle for fully hermetic, no-network
# compilation. Remove this line for online mode (fetches packages on first
# run from tectonic's CDN).
tectonic.bundle()
use_repo(tectonic, "rules_latex_tectonic_toolchains")
register_toolchains("@rules_latex_tectonic_toolchains//:all")
```

In a `BUILD.bazel`:

```python
load("@rules_latex//latex:defs.bzl",
     "latex_cache_snapshot", "latex_document", "latex_library", "latex_test")

latex_library(
    name = "preamble",
    srcs = ["preamble.tex"],
)

latex_document(
    name = "cv",
    main = "cv.tex",
    srcs = ["cv.tex"],
    deps = [":preamble"],
    # Optional: produce byte-identical PDFs across runs.
    reproducible = True,
    # Optional: build hermetically against a checked-in cache snapshot
    # produced by //:cv_snapshot below.
    cache = "cv_cache.tar.gz",
)

# Run once with internet to (re-)generate cv_cache.tar.gz:
#     bazel run //:cv_snapshot
latex_cache_snapshot(
    name = "cv_snapshot",
    main = "cv.tex",
    srcs = ["cv.tex"],
    deps = [":preamble"],
    output = "cv_cache.tar.gz",
)

# Regression test: fails if cv.tex stops compiling cleanly.
latex_test(
    name = "cv_compiles",
    main = "cv.tex",
    srcs = ["cv.tex"],
    deps = [":preamble"],
    cache = "cv_cache.tar.gz",
)
```

Then:

```bash
bazel run //:cv_snapshot     # once, with internet; commit cv_cache.tar.gz
bazel build //:cv            # subsequently, fully offline, ~seconds
bazel test //:cv_compiles
```

A complete, runnable example lives under [`example/`](./example).

## Rules

| Rule | Purpose |
|---|---|
| [`latex_document`](./latex/private/latex_document.bzl) | Compile a `.tex` file (plus its transitive sources) into a PDF (or other tectonic-supported format). |
| [`latex_library`](./latex/private/latex_library.bzl) | Group reusable LaTeX source files (preambles, custom style/class files) that other targets depend on. |
| [`latex_pkg`](./latex/private/latex_pkg.bzl) | Group non-LaTeX resources (images, fonts, `.bib` files) that documents may need. |
| [`latex_test`](./latex/private/latex_test.bzl) | Compile a document under `bazel test` and assert on patterns in the tectonic log file (e.g. fail on `LaTeX Error:`). |
| [`latex_cache_snapshot`](./latex/private/latex_cache_snapshot.bzl) | `bazel run`-able command that captures a small, per-document offline cache snapshot for hermetic builds. |

All five are loaded from `@rules_latex//latex:defs.bzl`.

## Supported platforms

`rules_latex` currently ships pinned Tectonic binaries for:

- Linux x86_64 (musl, statically linked)
- Linux aarch64 (musl, statically linked)
- macOS x86_64
- macOS aarch64 (Apple Silicon)
- Windows x86_64 (MSVC)

## Hermetic builds

`rules_latex` supports three modes, from fastest-to-set-up to
most-hermetic:

1. **Online** (the default). Tectonic fetches packages from
   `relay.fullyjustified.net` on first use and caches them per-action.
   Convenient for local dev; not suitable for CI or air-gapped builds.
2. **Full bundle** — add `tectonic.bundle()` to your `MODULE.bazel`.
   Pulls the pinned ~3 GB upstream bundle once, gives a hermetic
   `--bundle` + `--only-cached` invocation for every document.
3. **Per-document cache snapshot** — declare a `latex_cache_snapshot`
   for each document, run it once (`bazel run //:cv_cache`) to
   produce a checked-in ~10–100 MB tarball that contains exactly the
   packages your document needs. Subsequent builds use it via
   `latex_document(cache = "cv_cache.tar.gz")` and run fully offline
   in seconds.

The snapshot mode is the recommended option for repos that ship
specific documents: small, fast, content-addressed, and you only re-run
the snapshot when you add new `\\usepackage` lines.

## Design

For the architectural rationale and an outline of the v0.x → v1.0 roadmap, see
[`DESIGN.md`](./DESIGN.md).

## License

Apache License 2.0. See [`LICENSE`](./LICENSE).
