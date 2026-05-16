# rules_latex

Bazel rules for building LaTeX documents with the
[Tectonic](https://tectonic-typesetting.github.io/) typesetting engine.

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

## What you get

<div class="grid cards" markdown>

-   :material-rocket-launch:{ .lg .middle } __Zero-config builds__

    ---

    Drop a `latex_document` into a `BUILD.bazel`; the rule
    automatically primes a per-document package cache the first time
    you build, then runs every subsequent compile offline against it.
    No `DEPS = [...]` list, no `tectonic.bundle()` boilerplate.

    [:octicons-arrow-right-24: Getting started](getting-started/first-document.md)

-   :material-bookshelf:{ .lg .middle } __First-class bibliography__

    ---

    A vendored `biber` toolchain (pinned to match the bundle's
    biblatex) is staged onto PATH at compile time. Just
    `biber = True` and your `\addbibresource` directives Just Work.

    [:octicons-arrow-right-24: Bibliography](getting-started/bibliography.md)

-   :material-eye-arrow-right:{ .lg .middle } __Overleaf-style live preview__

    ---

    `bazel run //:cv_web` stands up a localhost HTTP server with
    PDF.js rendering. Edit `cv.tex`, see the PDF update within a
    second. Click anywhere in the PDF to jump to the corresponding
    source line via SyncTeX.

    [:octicons-arrow-right-24: Live preview](getting-started/live-preview.md)

-   :material-lock-check:{ .lg .middle } __Hermetic and reproducible__

    ---

    Every action is sandboxed; the tectonic binary, the package
    bundle, and biber are all content-addressed. Set
    `reproducible = True` for byte-identical PDFs across clean
    builds. CI verifies this on every push.

    [:octicons-arrow-right-24: Hermetic builds](concepts/hermetic-builds.md)

</div>

## Why a new ruleset?

The pre-existing
[`bazel_latex`](https://github.com/ProdriveTechnologies/bazel-latex)
wraps a traditional TeX Live install and requires you to declare
every LaTeX package you use as an explicit Bazel target. That's
hermetic but verbose: a typical thesis BUILD file ends up with a
30-entry `DEPS = [...]` list.

`rules_latex` takes a different approach. Tectonic resolves
`\usepackage{...}` directives from its own package bundle; we just
need to keep that bundle hermetically pinned and let Bazel cache the
per-document subset.

See the [Design rationale](about/design.md) for the full story.

## Compatibility

| Layer                | Pinned version |
|----------------------|----------------|
| **Bazel**            | 8.0+ (Bzlmod-only) |
| **Tectonic**         | 0.16.9 |
| **biber / biblatex** | 2.17 / 3.17 (paired by control-file format) |
| **TeX Live**         | 2022 (frozen — see [Roadmap](about/roadmap.md)) |

## Project status

| Layer | Status |
|---|---|
| Core rules (`document`, `library`, `pkg`, `test`) | Stable since v0.1.0 |
| Toolchain (`tectonic`, `bundle`, `biber`) | Stable since v0.2.0 |
| Live preview (`serve`, `serve_web`) | Stable since v0.2.0 |
| SyncTeX reverse-sync | Stable since v0.2.0 |
| Implicit cache pipeline | Stable since v0.2.0 |
| Self-hosted PDF.js | Stable since v0.2.0 |
| Modern biblatex (3.18+) | Blocked on upstream bundle refresh ([#1][issue-1]) |
| Linux arm64 biber | Pending v0.3 (build from source) |
| SyncTeX forward-sync | Future |

[issue-1]: https://github.com/nicklambourne/rules_latex/issues/1
