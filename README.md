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
load("@rules_latex//latex:defs.bzl", "latex_document", "latex_library", "latex_test")

latex_library(
    name = "preamble",
    srcs = ["preamble.tex"],
)

latex_document(
    name = "cv",
    main = "cv.tex",
    srcs = ["cv.tex"],
    deps = [":preamble"],
    # Enable biber for biblatex-using documents (thesis, papers, ...).
    biber = True,
)

# Regression test: fails if cv.tex stops compiling cleanly.
latex_test(
    name = "cv_compiles",
    main = "cv.tex",
    srcs = ["cv.tex"],
    deps = [":preamble"],
)
```

Then:

```bash
bazel build //:cv            # first build: ~30-90s online prime + offline compile
bazel build //:cv            # subsequent builds: ~1-5s (action cache hit)
bazel test //:cv_compiles
```

That's it — no `latex_cache_snapshot` target, no checked-in tarball,
no enumerated `@bazel_latex//packages:foo` deps. The rule transparently
populates a per-document cache from the pinned Tectonic bundle the
first time you build, then runs the actual compile offline against
that cache. Bazel's action cache makes subsequent builds (including
across machines via the remote cache) skip the online prime entirely.

For fully air-gapped builds, opt into a checked-in cache snapshot:

```python
# Run once with internet to (re-)generate cv_cache.tar.gz:
#     bazel run //:cv_snapshot
latex_cache_snapshot(
    name = "cv_snapshot",
    main = "cv.tex",
    srcs = ["cv.tex"],
    deps = [":preamble"],
    output = "cv_cache.tar.gz",
    biber = True,
)

latex_document(
    name = "cv",
    main = "cv.tex",
    srcs = ["cv.tex"],
    deps = [":preamble"],
    biber = True,
    cache = "cv_cache.tar.gz",   # skips the implicit pipeline entirely
)
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
| [`latex_serve`](./latex/private/latex_serve.bzl) | `bazel run`-able live-preview loop: watches the document's sources, rebuilds via `bazel build` on every save, opens the PDF in the system viewer. |
| [`latex_serve_web`](./latex/private/latex_serve_web.bzl) | Like `latex_serve`, but exposes the preview as a localhost HTTP page rendered with PDF.js — Overleaf-style in-browser preview with auto-refresh on save. |

All seven are loaded from `@rules_latex//latex:defs.bzl`.

## Live preview

For an Overleaf-style edit-and-see-it-update experience, declare a
`latex_serve` (system PDF viewer) or `latex_serve_web` (in-browser
preview) target alongside your document:

```python
latex_document(
    name = "cv",
    main = "cv.tex",
    srcs = ["cv.tex"],
    cache = "cv_cache.tar.gz",   # so live rebuilds are offline and fast
    synctex = True,              # click-to-source in latex_serve_web
)

# System-PDF-viewer flavour (lightest).
latex_serve(
    name = "cv_live",
    document = ":cv",
)

# In-browser flavour (Overleaf-like). PDF.js handles rendering (served
# from the self-hosted /_pdfjs/ endpoint, no CDN), the server pushes
# 'reload' events over Server-Sent Events on every successful rebuild,
# and scroll position is preserved across updates. Clicking on the PDF
# jumps to the source line via SyncTeX when `synctex = True` is set on
# the document.
latex_serve_web(
    name = "cv_web",
    document = ":cv",
)
```

Then in one terminal:

```bash
bazel run //:cv_live
# Watches cv.tex (and any latex_library/latex_pkg deps), rebuilds on
# every save, opens bazel-bin/cv.pdf in the system PDF viewer.
```

Or in a browser-driven workflow:

```bash
bazel run //:cv_web
# serving live preview at http://127.0.0.1:8765/
# (open the URL; edit cv.tex; the page auto-refreshes the PDF;
#  click anywhere in the PDF to see the corresponding source line)
```

Edit the source in your editor of choice; the PDF is rebuilt within a
second or so per change. The viewer's own auto-reload behaviour kicks
in (macOS Preview, Linux Evince/Okular all support this out of the
box).

Because the rebuild is just `bazel build //:cv` under the hood, it
shares the toolchain, sandbox, and cache snapshot with normal builds —
no "works locally, fails in CI" drift.

## Supported platforms

`rules_latex` currently ships pinned Tectonic binaries for:

- Linux x86_64 (musl, statically linked)
- Linux aarch64 (musl, statically linked)
- macOS x86_64
- macOS aarch64 (Apple Silicon)
- Windows x86_64 (MSVC)

## Hermetic builds

`rules_latex` supports four modes (the rule chooses automatically; in
priority order):

1. **Per-document checked-in cache snapshot** (most hermetic). Declare
   a `latex_cache_snapshot`, run it once with internet to produce a
   ~10–100 MB tarball, commit it, and set `cache = "foo.tar.gz"` on
   the document. Subsequent builds run fully offline. Best for
   air-gapped CI and byte-reproducible release builds.
2. **Full bundle**. Add `tectonic.bundle()` to your `MODULE.bazel`;
   the pinned 2.88 GB upstream bundle is fetched once via
   `download_and_extract` and used as `--bundle` for every compile.
   Fully hermetic, no per-document setup, but every fresh CI runner
   spends bandwidth pulling 2.88 GB.
3. **Implicit cache pipeline** (default, recommended). When neither
   (1) nor (2) is configured, the rule synthesises a one-time online
   prime per document, captures the resulting cache as a Bazel
   action output, and feeds it to a hermetic compile. Bazel's
   action cache means the prime is shared across local builds and
   CI runners via the remote cache. You write zero extra build-file
   plumbing for this.
4. **Pure online**. Only happens by accident — there's no flag for
   it. If you somehow disable the implicit pipeline and don't pin a
   bundle or cache, the legacy "fetch on every action" behaviour
   would kick in, which we don't recommend.

## LaTeX package versions

The pinned Tectonic bundle dates from 2022 (the upstream
`tectonic-texlive-bundles` project was archived in October 2024).
That's fine for ~95% of real-world LaTeX use — all major citation
styles (APA, Chicago, IEEE, Harvard, Vancouver), tikz, beamer,
biblatex, etc. were mature by then — but it does mean you don't get
biblatex 3.18+ or any CTAN packages released after September 2022.
See `DESIGN.md` §4.10 for the chain of causes and the five
solution options we've considered. Users who hit a specific
package's staleness can self-host a newer bundle today via
`tectonic.bundle()`.

## Design

For the architectural rationale and an outline of the v0.x → v1.0 roadmap, see
[`DESIGN.md`](./DESIGN.md).

## License

Apache License 2.0. See [`LICENSE`](./LICENSE).
