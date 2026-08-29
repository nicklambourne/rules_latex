# rules_latex

<p align="center">
  <img src="assets/logo.svg" alt="rules_latex logo" width="180" />
</p>

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

    `bazel run //:cv_live` stands up a localhost HTTP server with
    PDF.js rendering. Edit `cv.tex`, see the PDF update within a
    second. Click anywhere in the PDF to copy the corresponding source
    location; editors can also send a source location to the preview.

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
| **biber / biblatex** | 2.21 / 3.21 (paired by control-file format) |
| **TeX Live**         | 2026 (self-hosted bundle — see [Roadmap](about/roadmap.md)) |

## Project status

| Layer | Status |
|---|---|
| Core rules (`document`, `library`, `pkg`, `test`) | Stable since v0.1.0 |
| Toolchain (`tectonic`, `bundle`, `biber`) | Stable since v0.2.0 |
| Live preview (`latex_live`) | Stable since v0.2.0 (system-viewer `latex_serve` removed in v0.6.0) |
| SyncTeX reverse lookup (PDF → copied source location) | Stable since v0.2.0 |
| Implicit cache pipeline | Stable since v0.2.0 |
| Self-hosted PDF.js | Stable since v0.2.0 |
| `ctan_packages` (auto-resolved transitive closure) | Stable since v0.4.2 |
| Self-hosted TeX Live 2026 bundle (biblatex 3.21 / biber 2.21) | Stable since v0.6.0 |
| SyncTeX forward-sync | Stable since v0.4.2 |
| WebSocket push transport for live preview | Stable since v0.5.0 |
| In-doc search, outline sidebar, build-log drawer, theme toggle | Stable since v0.5.0 |
| Linux arm64 biber | Vendored (prebuilt biber 2.21) since v0.6.0 |
| Fast-path live rebuilds (`serve_fast`) | Stable since v0.6.1 |
| Long-document preview performance | Stable on `master` ([#50][issue-50]) |
| Hermetic private Python 3.13 toolchain | Stable on `master` |
| Bazel Central Registry publication | [v0.6.1 available in the BCR][bcr-module] |

[issue-10]: https://github.com/nicklambourne/rules_latex/issues/10
[issue-50]: https://github.com/nicklambourne/rules_latex/issues/50
[bcr-module]: https://registry.bazel.build/modules/rules_latex/
