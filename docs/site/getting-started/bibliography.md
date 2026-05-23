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
By default `rules_latex` pins biber 2.17 to match the biblatex 3.17
that ships in the upstream Tectonic bundle. You can't use a newer
biber against that bundle's biblatex.

If you need biblatex 3.18+ features (most modern citation styles —
APA 7th edition, Chicago, IEEE, Nature — sit above that line), set
`modern_biblatex = True` on the toolchain in `MODULE.bazel`. That
fetches biblatex 3.21 + biber 2.21 and overlays them. See
"[Modern citation styles](#modern-citation-styles)" below.

## Citation styles not in the bundle

The bundle ships only the five core biblatex styles: `numeric`,
`alphabetic`, `authoryear`, `authortitle`, and `verbose`. Anything
else — APA, Chicago, IEEE, Nature, Vancouver, etc. — lives in
separate CTAN packages.

For those, you'll need **both** the `ctan_packages` attribute on
your document **and** the `modern_biblatex` toolchain opt-in —
because the extension styles are versioned ahead of the bundle's
pinned biblatex.

## Modern citation styles

To use modern biblatex extension styles, opt the toolchain into the
newer biblatex / biber pair in your workspace's `MODULE.bazel`:

```python
tectonic = use_extension("@rules_latex//latex/toolchain:extensions.bzl", "tectonic")
tectonic.toolchain(modern_biblatex = True)
use_repo(tectonic, "rules_latex_tectonic_toolchains")
register_toolchains("@rules_latex_tectonic_toolchains//:all")
```

Then list the style in `ctan_packages` and use it in your
document:

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

Background: the modern style files (`apa.bbx`, `chicago.bbx`, …)
need biblatex 3.18+ and biber 2.18+. Without `modern_biblatex = True`,
the fetched style files reach tectonic but the bundle's older
biblatex chokes on the newer control-file format. See the
[CTAN packages](ctan-packages.md#modern-biblatex-extension-styles)
page for the full discussion of the version coupling and when *not*
to opt in.

## Platform support

| Platform        | Toolchain biber? | Note |
|-----------------|------------------|------|
| Linux x86_64    | :material-check: | Upstream prebuilt |
| Linux aarch64   | :material-close: | Use `biber_strategy = "system"` |
| macOS x86_64    | :material-check: | Universal binary |
| macOS aarch64   | :material-check: | Universal binary |
| Windows x86_64  | :material-check: | |

### Linux arm64 workaround

The upstream biber project doesn't ship a Linux arm64 binary, so the
toolchain has a gap there. Install biber via your distro
(`apt-get install biber`) and set:

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
installed — but it's the only option until v0.3 ships a built-from-
source biber for that platform.
