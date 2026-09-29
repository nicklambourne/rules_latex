# Your first document

This walkthrough goes from "empty directory" to "live-reloading PDF
viewer" in about 10 minutes.

## Set up the workspace

```bash
mkdir my-cv && cd my-cv
git init
```

Create the three Bazel control files:

=== "MODULE.bazel"

    ```python
    module(name = "my-cv")

    bazel_dep(name = "rules_latex", version = "0.7.0")

    tectonic = use_extension("@rules_latex//latex/toolchain:extensions.bzl", "tectonic")
    tectonic.toolchain()
    use_repo(tectonic, "rules_latex_tectonic_toolchains")
    register_toolchains("@rules_latex_tectonic_toolchains//:all")
    ```

=== ".bazelrc"

    ```
    common --enable_bzlmod
    common --noenable_workspace
    build --verbose_failures
    ```

=== ".bazelversion"

    ```
    8.0.0
    ```

## Write the document

Create `cv.tex`:

```latex
\documentclass[11pt,a4paper]{article}
\usepackage[margin=2cm]{geometry}
\usepackage[hidelinks]{hyperref}

\begin{document}

\begin{center}
    {\Huge Your Name} \\[6pt]
    \href{mailto:you@example.com}{you@example.com} $\cdot$
    \href{https://example.com}{example.com}
\end{center}

\section*{Experience}

\textbf{Example Corp} \hfill 2023 -- Present \\
\textit{Software Engineer}

\begin{itemize}
    \item Did interesting things.
    \item With other interesting people.
\end{itemize}

\end{document}
```

Create the `BUILD.bazel`:

```python
load(
    "@rules_latex//latex:defs.bzl",
    "latex_document",
    "latex_live",
    "latex_test",
)

latex_document(
    name = "cv",
    main = "cv.tex",
    srcs = ["cv.tex"],
    synctex = True,    # click PDF -> copy <file>:<line> to clipboard
)

# `bazel run //:cv_live` — live preview in the browser
latex_live(
    name = "cv_live",
    document = ":cv",
)

# `bazel test //:cv_compiles` — catches regressions in CI
latex_test(
    name = "cv_compiles",
    main = "cv.tex",
    srcs = ["cv.tex"],
)
```

## Build it

```bash
bazel build //:cv
```

First run takes ~30 seconds (tectonic prime + compile); subsequent
builds are <5 seconds. The PDF lives at `bazel-bin/cv.pdf`.

`latex_document(outfmt = "xdv")` produces an XDV file and
`latex_document(outfmt = "aux")` produces the auxiliary file instead.
These are single-file outputs; see the offline `hello_xdv` and
`hello_aux` targets in `examples/hello/BUILD.bazel`. `latex_test` supports
PDF and XDV, which produce a TeX log for its assertions. AUX does not
produce a log. HTML is not currently supported: Tectonic's converter can
emit multiple assets, while `latex_document` declares one output file.

## Iterate with the live preview

In one terminal:

```bash
bazel run //:cv_live
# serving live preview at http://127.0.0.1:8765/
```

Open the URL in your browser. Edit `cv.tex` in your editor of choice;
every save triggers a rebuild and the page auto-refreshes. Click
anywhere in the rendered PDF to see the corresponding source line
echoed in the footer bar.

## Lock the build in CI

```bash
bazel test //:cv_compiles
```

This compiles `cv.tex` in CI and fails the build if the LaTeX log
contains any of the standard error patterns (`LaTeX Error:`, `Emergency
stop`, etc.). It's a one-liner that catches the day a misplaced
backslash silently breaks your document.

## Next steps

- [Add bibliographies](bibliography.md) with biblatex + biber.
- Explore [hermetic builds](../concepts/hermetic-builds.md) for
  air-gapped or fully-reproducible environments.
- Browse the [build rules API reference](../api/rules.md).
