# Installation

`rules_latex` is a Bazel module. Add it to your `MODULE.bazel`:

```python
bazel_dep(name = "rules_latex", version = "0.1.0")

tectonic = use_extension("@rules_latex//latex/toolchain:extensions.bzl", "tectonic")
tectonic.toolchain()
use_repo(tectonic, "rules_latex_tectonic_toolchains")
register_toolchains("@rules_latex_tectonic_toolchains//:all")
```

That's the entire setup. The first time you build a `latex_document`,
Bazel will:

1. Download the pinned Tectonic binary for your platform (~20 MB).
2. Run Tectonic once against your sources to prime a cache (online).
3. Re-run Tectonic offline against the primed cache to produce the PDF.

The first build typically takes 30–90 seconds on a fast connection;
subsequent builds use Bazel's action cache and complete in under five
seconds.

## Prerequisites

- **Bazel 8.0+** (Bzlmod must be enabled). `rules_latex` is Bzlmod-only
  by design.
- An internet connection on first build (for the package prime).
  Subsequent builds are fully offline.

## Verifying the install

Create `cv.tex`:

```latex
\documentclass{article}
\begin{document}
Hello, rules\_latex!
\end{document}
```

And a `BUILD.bazel`:

```python
load("@rules_latex//latex:defs.bzl", "latex_document")

latex_document(
    name = "cv",
    main = "cv.tex",
    srcs = ["cv.tex"],
)
```

Then:

```bash
bazel build //:cv
```

You should end up with `bazel-bin/cv.pdf`.

## Optional: opt into the full upstream bundle

By default, `rules_latex` uses its implicit cache pipeline (a one-time
online prime per document, then offline forever). If you'd prefer the
full ~3 GB upstream bundle approach — useful for monorepos with many
documents that share most of the same packages — add:

```python
tectonic.bundle()
```

after `tectonic.toolchain()` in your `MODULE.bazel`. The 3 GB bundle
is fetched once and used for every compile, skipping the per-document
prime.

See [Hermetic builds](../concepts/hermetic-builds.md) for the full
mode hierarchy.
