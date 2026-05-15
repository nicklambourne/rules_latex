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
use_repo(tectonic, "rules_latex_tectonic_toolchains")
register_toolchains("@rules_latex_tectonic_toolchains//:all")
```

In a `BUILD.bazel`:

```python
load("@rules_latex//latex:defs.bzl", "latex_document", "latex_library")

latex_library(
    name = "preamble",
    srcs = ["preamble.tex"],
)

latex_document(
    name = "cv",
    main = "cv.tex",
    srcs = ["cv.tex"],
    deps = [":preamble"],
)
```

Then:

```bash
bazel build //:cv
# bazel-bin/cv.pdf is your document.
```

A complete, runnable example lives under [`example/`](./example).

## Rules

| Rule | Purpose |
|---|---|
| [`latex_document`](./latex/private/latex_document.bzl) | Compile a `.tex` file (plus its transitive sources) into a PDF (or other tectonic-supported format). |
| [`latex_library`](./latex/private/latex_library.bzl) | Group reusable LaTeX source files (preambles, custom style/class files) that other targets depend on. |
| [`latex_pkg`](./latex/private/latex_pkg.bzl) | Group non-LaTeX resources (images, fonts, `.bib` files) that documents may need. |

All three are loaded from `@rules_latex//latex:defs.bzl`.

## Supported platforms

`rules_latex` currently ships pinned Tectonic binaries for:

- Linux x86_64 (gnu)
- Linux aarch64 (musl)
- macOS x86_64
- macOS aarch64 (Apple Silicon)

Windows support is planned but not yet wired up.

## Design

For the architectural rationale and an outline of the v0.x → v1.0 roadmap, see
[`DESIGN.md`](./DESIGN.md).

## License

Apache License 2.0. See [`LICENSE`](./LICENSE).
