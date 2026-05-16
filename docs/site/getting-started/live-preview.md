# Live preview

`rules_latex` ships two live-preview rules for the
"edit-the-source-watch-the-PDF-update" workflow. Both watch the
document's transitive sources via `LatexInfo` and rebuild via
`bazel build` on every save.

## `latex_serve` — system PDF viewer

```python
load("@rules_latex//latex:defs.bzl", "latex_document", "latex_serve")

latex_document(name = "cv", main = "cv.tex", srcs = ["cv.tex"])

latex_serve(
    name = "cv_live",
    document = ":cv",
)
```

```bash
bazel run //:cv_live
# Watching cv.tex
# Built bazel-bin/cv.pdf in 3.2s. Opened in Preview.
```

The PDF opens once in your system viewer (Preview on macOS,
`xdg-open` on Linux, `start` on Windows). Subsequent rebuilds rely on
the viewer's own auto-reload behaviour:

| Viewer | Auto-reload? |
|--------|---------------|
| macOS Preview | :material-check: |
| Linux Evince | :material-check: |
| Linux Okular | :material-check: |
| Adobe Reader | :material-close: (locks the file) |
| Chrome PDF viewer | :material-close: (manual refresh) |

## `latex_serve_web` — in-browser preview

For an Overleaf-style experience, declare a `latex_serve_web` target:

```python
load("@rules_latex//latex:defs.bzl", "latex_document", "latex_serve_web")

latex_document(
    name = "cv",
    main = "cv.tex",
    srcs = ["cv.tex"],
    synctex = True,   # enables click-to-source
)

latex_serve_web(
    name = "cv_web",
    document = ":cv",
)
```

```bash
bazel run //:cv_web
# serving live preview at http://127.0.0.1:8765/
```

Open the URL in your browser. The page:

- Renders the PDF with [PDF.js](https://mozilla.github.io/pdf.js/)
  (vendored, no CDN dependency).
- Listens for "reload" events over Server-Sent Events.
- Preserves scroll position across reloads.
- When `synctex = True` is set on the document, clicking anywhere in
  the rendered PDF resolves to a source `file:line` displayed in the
  footer bar.

## How fast is the loop?

For a small document (single-page CV, hello-world) paired with a
cache snapshot, steady-state rebuilds complete in **200–400 ms**.
First build is slower (the online prime takes ~30 s) but happens
exactly once per content-hash of the inputs — Bazel's action cache
handles the rest.

For larger documents (multi-chapter thesis, paper with figures), the
TeX compile itself dominates and rebuilds run in 2–5 s.

## What gets watched?

The watcher monitors every `.tex`, `.bib`, image, and other file in
the document's `srcs` plus transitively via `deps`. Edits to the
toolchain binary or the cache snapshot are picked up by Bazel's
analysis layer, so they trigger correct rebuilds too.

External-repo files (e.g. from a `latex_library` published in another
Bazel module) are not watched. Edit those and re-run `bazel run
//:cv_web` to pick up the change.

## Architecture

Both rules synthesise a small launcher script that:

1. Polls the watched paths every 250 ms via `os.stat`.
2. Shells out to `bazel build <document_label>` on change.
3. `latex_serve` opens the PDF once; `latex_serve_web` keeps a tiny
   HTTP server alive and pushes SSE events to connected browser tabs.

Both use the same `bazel build` invocation as a normal build, which
means **live-mode behaviour is identical to CI** — no "works locally,
fails in CI" drift. See
[DESIGN.md §4.7](https://github.com/nicklambourne/rules_latex/blob/master/DESIGN.md#47-live-preview)
for the rationale.
