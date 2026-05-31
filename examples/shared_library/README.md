# Shared-library example

A miniature monorepo showing how to **share LaTeX infrastructure across
packages** — the pattern you reach for as soon as you have more than
one document.

A fictional "Meridian Field Station" keeps its house style,
bibliography, and logo in dedicated library packages, and two unrelated
documents (a report and a memo) consume them:

```
shared_library/
├── lib/
│   ├── preamble/   latex_library  → preamble.tex   (house style: colours, fonts, macros, biblatex config)
│   └── bib/        latex_library  → references.bib  (shared bibliography)
├── report/         latex_document → main.tex + sections/*.tex   (deps: preamble, bib, //_shared/logo)
└── memo/           latex_document → main.tex                    (deps: preamble, bib, //_shared/logo)
```

(The logo comes from `//_shared/logo`, a `latex_pkg` already shared by
the other examples.)

## What it demonstrates

- **`latex_library` for shared sources.** `lib/preamble` and `lib/bib`
  expose a `.tex` and a `.bib` that any number of documents can `deps`
  on. Edit the house style once; every document updates.
- **Cross-package source staging (DESIGN.md §4.11).** The documents
  reference shared files by their *workspace-relative path*:

  ```latex
  \input{shared_library/lib/preamble/preamble}
  \addbibresource{shared_library/lib/bib/references.bib}
  \includegraphics{_shared/logo/logo.png}   % via the shared \doctitle macro
  ```

  Those files live in *other* packages, but rules_latex's main-rooted
  staging lays them down at exactly those paths in the compile work
  directory, so the references resolve with no copying.
- **One library, many documents.** `report` and `memo` are different
  document shapes that share the same preamble, bibliography, and logo.

## Why path-references, not `\usepackage`

Shared infrastructure is pulled in by **path** (`\input`,
`\addbibresource`, `\includegraphics`) rather than by `\usepackage`-ing
a cross-package `.sty`. tectonic only searches the current directory
and the bundle when resolving `\usepackage{name}`, so a `.sty` sitting
in a sibling package wouldn't be found — but a path-based `\input`
always resolves against the staged work directory. A shared "house
style" is therefore a `preamble.tex` you `\input`, which can do
everything a `.sty` would (load packages, define macros, set colours
and section formatting).

## Build it

```sh
cd examples
bazel build //shared_library/report:report //shared_library/memo:memo
bazel test  //shared_library/report:report_compiles //shared_library/memo:memo_compiles
```
