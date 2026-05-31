# Bibliography

LaTeX documents that use `\cite{}` need a bibliography processor.
Modern documents almost always want
[biblatex](https://ctan.org/pkg/biblatex) + biber, which handle
Unicode, sophisticated styles, and multi-bibliography workflows
correctly.

`rules_latex` ships a vendored biber binary alongside the tectonic
toolchain. To use it, set `biber = True` on your document.

## Minimal example

```latex
% paper.tex
\documentclass{article}
\usepackage[backend=biber,style=numeric]{biblatex}
\addbibresource{references.bib}

\begin{document}
This sentence cites Knuth~\cite{knuth1984}.
\printbibliography
\end{document}
```

```bibtex
% references.bib
@book{knuth1984,
  author = {Donald E. Knuth},
  title  = {The {{\TeX}}book},
  publisher = {Addison-Wesley},
  year = {1984},
}
```

```python
# BUILD.bazel
load("@rules_latex//latex:defs.bzl", "latex_document")

latex_document(
    name = "paper",
    main = "paper.tex",
    srcs = ["paper.tex", "references.bib"],
    biber = True,
)
```

```bash
bazel build //:paper
```

The build runs tectonic, biber, tectonic again — all sandboxed, all
hermetic. The resulting PDF has resolved citations.

## How it works

When `biber = True`:

1. The platform-specific `biber` binary from the toolchain is staged
   into a per-action scratch directory.
2. That directory is prepended to PATH inside the sandbox.
3. Tectonic's biblatex subprocess resolves `biber` by basename and
   shells out to it as usual.

The biber binary is vendored from a [GitHub release
mirror](https://github.com/nicklambourne/rules_latex/releases) on the
`rules_latex` repo, content-addressed by SHA-256. See
[DESIGN.md §4.9](https://github.com/nicklambourne/rules_latex/blob/master/DESIGN.md#49-biber)
for the full implementation details.

## Version coupling

Biber is tightly coupled to biblatex's "control file format" version.
`rules_latex` pins biber 2.21 to match the biblatex 3.21 that ships in
the self-hosted TeX Live 2026 bundle. The pin and the bundle are
bumped together, so the pair can never drift — you don't manage it.

## Citation styles not in the bundle

The bundle ships the standard biblatex styles (`numeric`,
`alphabetic`, `authoryear`, `authortitle`, `verbose`). Extension
styles — APA, Chicago, IEEE, Nature, Vancouver, etc. — live in
separate CTAN packages. Add them with the `ctan_packages` attribute;
no toolchain opt-in is needed, because the bundle's biblatex 3.21 is
new enough to process them.

## Modern citation styles

Just list the style package in `ctan_packages` and turn on `biber`:

```python
latex_document(
    name = "thesis",
    main = "thesis.tex",
    srcs = ["thesis.tex", "references.bib"],
    ctan_packages = ["biblatex-apa"],   # APA 7th edition style
    biber = True,
)
```

```latex
% thesis.tex
\usepackage[style=apa]{biblatex}
```

Modern extension styles (`apa.bbx`, `chicago.bbx`, …) need biblatex
3.18+ / biber 2.18+; the TeX Live 2026 bundle ships 3.21 / 2.21, so
they work with no extra configuration. See the
[CTAN packages](ctan-packages.md#modern-biblatex-extension-styles)
page for the full version-coupling discussion.

> **Upgrading from ≤ v0.5?** Earlier versions required a
> `tectonic.toolchain(modern_biblatex = True)` opt-in for these
> styles. It was **removed** in v0.6.0 — the rebuilt bundle ships the
> modern stack natively. Delete that argument from your `MODULE.bazel`.

## Platform support

| Platform        | Toolchain biber? | Note |
|-----------------|------------------|------|
| Linux x86_64    | :material-check: | Upstream prebuilt |
| Linux aarch64   | :material-check: | Prebuilt biber 2.21 (CTAN) |
| macOS x86_64    | :material-check: | Universal binary |
| macOS aarch64   | :material-check: | Universal binary |
| Windows x86_64  | :material-check: | |

### Unsupported platforms

biber 2.21 is vendored for every platform above, including Linux
arm64 (a prebuilt binary from CTAN's `biber-linux-aarch64` package,
new in v0.6.0). If you're on a platform without a vendored binary,
install biber via your distro (`apt-get install biber`) and fall back
to the system binary on `PATH`:

```python
latex_document(
    name = "thesis",
    main = "thesis.tex",
    srcs = [...],
    biber = True,
    biber_strategy = "system",   # use system biber on PATH
)
```

This is less hermetic — your build depends on whatever biber is
installed — so it's an escape hatch, not the recommended path.
