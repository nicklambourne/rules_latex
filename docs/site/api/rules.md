<!-- Generated with Stardoc: http://skydoc.bazel.build -->

Public API for rules_latex.

Load symbols from here:

    load("@rules_latex//latex:defs.bzl",
         "latex_document", "latex_library", "latex_pkg",
         "latex_test", "latex_cache_snapshot",
         "latex_serve", "latex_serve_web")


## Rules

- [latex_cache_snapshot](#latex_cache_snapshot)
- [latex_document](#latex_document)
- [latex_library](#latex_library)
- [latex_pkg](#latex_pkg)
- [latex_serve](#latex_serve)
- [latex_serve_web](#latex_serve_web)
- [latex_test](#latex_test)

## Providers

- [LatexInfo](#LatexInfo)


<a id="latex_cache_snapshot"></a>

## latex_cache_snapshot

<pre>
load("@rules_latex//latex:defs.bzl", "latex_cache_snapshot")

latex_cache_snapshot(<a href="#latex_cache_snapshot-name">name</a>, <a href="#latex_cache_snapshot-deps">deps</a>, <a href="#latex_cache_snapshot-srcs">srcs</a>, <a href="#latex_cache_snapshot-biber">biber</a>, <a href="#latex_cache_snapshot-main">main</a>, <a href="#latex_cache_snapshot-output">output</a>, <a href="#latex_cache_snapshot-pkg_files">pkg_files</a>)
</pre>

Bazel-run target that captures a tectonic cache snapshot.

**ATTRIBUTES**


| Name  | Description | Type | Mandatory | Default |
| :------------- | :------------- | :------------- | :------------- | :------------- |
| <a id="latex_cache_snapshot-name"></a>name |  A unique name for this target.   | <a href="https://bazel.build/concepts/labels#target-names">Name</a> | required |  |
| <a id="latex_cache_snapshot-deps"></a>deps |  Other targets that contribute LaTeX sources.   | <a href="https://bazel.build/concepts/labels">List of labels</a> | optional |  `[]`  |
| <a id="latex_cache_snapshot-srcs"></a>srcs |  All LaTeX source files needed to compile the document online. The cache snapshot will contain whatever tectonic decides to fetch for this compile, so make sure this list is realistic.   | <a href="https://bazel.build/concepts/labels">List of labels</a> | required |  |
| <a id="latex_cache_snapshot-biber"></a>biber |  If True, prime the cache with biber on PATH so the resulting snapshot contains bibliography-related files. Required when consumers compile biblatex documents against this snapshot.   | Boolean | optional |  `False`  |
| <a id="latex_cache_snapshot-main"></a>main |  The top-level .tex file passed to tectonic. Must also appear in `srcs`.   | <a href="https://bazel.build/concepts/labels">Label</a> | required |  |
| <a id="latex_cache_snapshot-output"></a>output |  Destination path for the snapshot tarball, relative to the workspace root.   | String | required |  |
| <a id="latex_cache_snapshot-pkg_files"></a>pkg_files |  Map of label-of-input -> staged-relative-path. Overrides the auto-staging path for the listed inputs, letting you place a file anywhere under main.tex's work directory. Typical use: stage a cross-package `.bib` file as a sibling of main.tex so `\addbibresource{refs.bib}` works without `..` (which tectonic refuses to hand to external tools).   | <a href="https://bazel.build/rules/lib/core/dict">Dictionary: Label -> String</a> | optional |  `{}`  |


<a id="latex_document"></a>

## latex_document

<pre>
load("@rules_latex//latex:defs.bzl", "latex_document")

latex_document(<a href="#latex_document-name">name</a>, <a href="#latex_document-deps">deps</a>, <a href="#latex_document-srcs">srcs</a>, <a href="#latex_document-biber">biber</a>, <a href="#latex_document-biber_strategy">biber_strategy</a>, <a href="#latex_document-cache">cache</a>, <a href="#latex_document-main">main</a>, <a href="#latex_document-outfmt">outfmt</a>, <a href="#latex_document-pkg_files">pkg_files</a>,
               <a href="#latex_document-reproducible">reproducible</a>, <a href="#latex_document-synctex">synctex</a>, <a href="#latex_document-tectonic_args">tectonic_args</a>)
</pre>

Compiles a LaTeX source tree using tectonic.

**ATTRIBUTES**


| Name  | Description | Type | Mandatory | Default |
| :------------- | :------------- | :------------- | :------------- | :------------- |
| <a id="latex_document-name"></a>name |  A unique name for this target.   | <a href="https://bazel.build/concepts/labels#target-names">Name</a> | required |  |
| <a id="latex_document-deps"></a>deps |  Other targets that contribute LaTeX sources (typically `latex_library` or `latex_pkg`).   | <a href="https://bazel.build/concepts/labels">List of labels</a> | optional |  `[]`  |
| <a id="latex_document-srcs"></a>srcs |  All LaTeX source files (.tex, .sty, .cls, .bib, images, etc.) that the document compilation might reference.   | <a href="https://bazel.build/concepts/labels">List of labels</a> | required |  |
| <a id="latex_document-biber"></a>biber |  Enable biber bibliography processing. When True, tectonic can shell out to a `biber` binary at compile time, resolving `\\addbibresource`/`\\bibliography` directives via biblatex. The binary comes from the rules_latex toolchain by default; set `biber_strategy = "system"` to use a system install instead.   | Boolean | optional |  `False`  |
| <a id="latex_document-biber_strategy"></a>biber_strategy |  Which biber binary to use when `biber = True`. `"toolchain"` (default) uses the rules_latex-vendored biber 2.17; fails at analysis time on platforms without an upstream prebuilt (currently linux/aarch64). `"system"` propagates $PATH so a system-installed biber is found; less hermetic, intended as an escape hatch.   | String | optional |  `"toolchain"`  |
| <a id="latex_document-cache"></a>cache |  Optional cache snapshot tarball (typically produced by `latex_cache_snapshot` and checked into the repository). When set, the action extracts the snapshot into the compile-time `TECTONIC_CACHE_DIR` and runs with `--only-cached`, giving a fully offline, hermetic build without pulling the full ~3 GB tectonic bundle or running an online prime. Takes precedence over the toolchain-level `tectonic.bundle()` and over the implicit cache pipeline.   | <a href="https://bazel.build/concepts/labels">Label</a> | optional |  `None`  |
| <a id="latex_document-main"></a>main |  The top-level .tex file passed to tectonic. Must also appear in `srcs`.   | <a href="https://bazel.build/concepts/labels">Label</a> | required |  |
| <a id="latex_document-outfmt"></a>outfmt |  Output format. Passed to `tectonic -X compile --outfmt`.   | String | optional |  `"pdf"`  |
| <a id="latex_document-pkg_files"></a>pkg_files |  Override the staged path of specific inputs. Map of label -> relative path under main.tex's work directory. The default main-rooted staging layout puts each src at a sensible place automatically; use `pkg_files` when the default would force you to write a long or `..`-containing path in main.tex. The classic case is a cross-package bib file: declare `pkg_files = {"//lib/refs:refs.bib": "refs.bib"}` and then write `\\addbibresource{refs.bib}` in main.tex.   | <a href="https://bazel.build/rules/lib/core/dict">Dictionary: Label -> String</a> | optional |  `{}`  |
| <a id="latex_document-reproducible"></a>reproducible |  When True, run tectonic in deterministic mode and set SOURCE_DATE_EPOCH=0, producing byte-identical output across runs given identical inputs. Off by default to keep PDF metadata (creation date) reflecting the actual build time. Mutually exclusive with `synctex`.   | Boolean | optional |  `False`  |
| <a id="latex_document-synctex"></a>synctex |  When True, tectonic is invoked with --synctex and the resulting `<name>.synctex.gz` is exposed as an additional output (also surfaced via the `synctex` OutputGroup). Consumed by `latex_serve_web` for click-to-source reverse-sync in the browser. Mutually exclusive with `reproducible` because tectonic's deterministic mode disables SyncTeX output.   | Boolean | optional |  `False`  |
| <a id="latex_document-tectonic_args"></a>tectonic_args |  Extra command-line arguments passed to tectonic. Use sparingly; prefer rule-level attributes when possible.   | List of strings | optional |  `[]`  |


<a id="latex_library"></a>

## latex_library

<pre>
load("@rules_latex//latex:defs.bzl", "latex_library")

latex_library(<a href="#latex_library-name">name</a>, <a href="#latex_library-deps">deps</a>, <a href="#latex_library-srcs">srcs</a>)
</pre>

A reusable collection of LaTeX source files.

**ATTRIBUTES**


| Name  | Description | Type | Mandatory | Default |
| :------------- | :------------- | :------------- | :------------- | :------------- |
| <a id="latex_library-name"></a>name |  A unique name for this target.   | <a href="https://bazel.build/concepts/labels#target-names">Name</a> | required |  |
| <a id="latex_library-deps"></a>deps |  Other latex_library / latex_pkg targets this library depends on.   | <a href="https://bazel.build/concepts/labels">List of labels</a> | optional |  `[]`  |
| <a id="latex_library-srcs"></a>srcs |  LaTeX source files (.tex/.sty/.cls/etc.) exposed by this library.   | <a href="https://bazel.build/concepts/labels">List of labels</a> | required |  |


<a id="latex_pkg"></a>

## latex_pkg

<pre>
load("@rules_latex//latex:defs.bzl", "latex_pkg")

latex_pkg(<a href="#latex_pkg-name">name</a>, <a href="#latex_pkg-srcs">srcs</a>)
</pre>

A bundle of resource files (images, bib, fonts) consumed by documents.

**ATTRIBUTES**


| Name  | Description | Type | Mandatory | Default |
| :------------- | :------------- | :------------- | :------------- | :------------- |
| <a id="latex_pkg-name"></a>name |  A unique name for this target.   | <a href="https://bazel.build/concepts/labels#target-names">Name</a> | required |  |
| <a id="latex_pkg-srcs"></a>srcs |  Resource files exposed by this package.   | <a href="https://bazel.build/concepts/labels">List of labels</a> | required |  |


<a id="latex_serve"></a>

## latex_serve

<pre>
load("@rules_latex//latex:defs.bzl", "latex_serve")

latex_serve(<a href="#latex_serve-name">name</a>, <a href="#latex_serve-document">document</a>, <a href="#latex_serve-open_pdf">open_pdf</a>, <a href="#latex_serve-poll_interval_ms">poll_interval_ms</a>)
</pre>

Watch a latex_document's sources and rebuild on every save.

**ATTRIBUTES**


| Name  | Description | Type | Mandatory | Default |
| :------------- | :------------- | :------------- | :------------- | :------------- |
| <a id="latex_serve-name"></a>name |  A unique name for this target.   | <a href="https://bazel.build/concepts/labels#target-names">Name</a> | required |  |
| <a id="latex_serve-document"></a>document |  The latex_document (or any rule providing LatexInfo) to watch and rebuild.   | <a href="https://bazel.build/concepts/labels">Label</a> | required |  |
| <a id="latex_serve-open_pdf"></a>open_pdf |  If True, open the built PDF in the system's default viewer after the first successful build.   | Boolean | optional |  `True`  |
| <a id="latex_serve-poll_interval_ms"></a>poll_interval_ms |  How often the watcher checks for source-file changes, in milliseconds. Polling-based to avoid third-party dependencies; bumping this trades latency for CPU.   | Integer | optional |  `250`  |


<a id="latex_serve_web"></a>

## latex_serve_web

<pre>
load("@rules_latex//latex:defs.bzl", "latex_serve_web")

latex_serve_web(<a href="#latex_serve_web-name">name</a>, <a href="#latex_serve_web-debounce_max_ms">debounce_max_ms</a>, <a href="#latex_serve_web-debounce_ms">debounce_ms</a>, <a href="#latex_serve_web-document">document</a>, <a href="#latex_serve_web-open_on_start">open_on_start</a>, <a href="#latex_serve_web-poll_interval_ms">poll_interval_ms</a>, <a href="#latex_serve_web-port">port</a>)
</pre>

Browser-based live-preview server for a latex_document.

**ATTRIBUTES**


| Name  | Description | Type | Mandatory | Default |
| :------------- | :------------- | :------------- | :------------- | :------------- |
| <a id="latex_serve_web-name"></a>name |  A unique name for this target.   | <a href="https://bazel.build/concepts/labels#target-names">Name</a> | required |  |
| <a id="latex_serve_web-debounce_max_ms"></a>debounce_max_ms |  Safety net for the debouncer: never wait more than this many milliseconds before firing a build, even if changes keep arriving. Without this cap, a user typing continuously into an editor with fast-autosave-on-every-keystroke would never see a rebuild. 1500 ms matches the upper bound where a user typically expects 'okay, something should happen now'.   | Integer | optional |  `1500`  |
| <a id="latex_serve_web-debounce_ms"></a>debounce_ms |  How many milliseconds of source-idle to require after a detected change before triggering a rebuild. Coalesces bursts of writes (e.g. format-on-save then user-save, or editors that write multiple files near-simultaneously) into a single build. Set to 0 to disable debouncing (rebuild on every poll-detected change; reproduces pre-v0.3.3 behaviour). The default of 250 ms is invisible to the user because the build itself takes longer than the debounce window.   | Integer | optional |  `250`  |
| <a id="latex_serve_web-document"></a>document |  The latex_document (or any rule providing LatexInfo) to watch and rebuild.   | <a href="https://bazel.build/concepts/labels">Label</a> | required |  |
| <a id="latex_serve_web-open_on_start"></a>open_on_start |  If True, open the preview automatically once the server starts. When the launching terminal belongs to a VS Code-family editor (VS Code, Cursor, VSCodium â detected via TERM_PROGRAM), the preview is opened as a Simple Browser tab in that editor via its CLI (`code --open-url`, `cursor --open-url`, `codium --open-url`). Otherwise it falls back to the system default web browser. JetBrains IDEs and other terminals without a Simple Browser equivalent fall back to the web-browser path. The plain http URL is always printed regardless, so users can copy/paste manually.   | Boolean | optional |  `False`  |
| <a id="latex_serve_web-poll_interval_ms"></a>poll_interval_ms |  How often the watcher checks for source-file changes, in milliseconds. The watcher is a polling loop (no third-party `watchdog`/inotify dependency), so this is the amortised cost of one stat() per watched file per interval. 80 ms keeps perceived save-to-preview latency under 100 ms while staying cheap. Independent of `debounce_ms`: the poll interval is how fast we *notice* a change; the debounce window is how long we *wait* after a change before triggering a build.   | Integer | optional |  `80`  |
| <a id="latex_serve_web-port"></a>port |  TCP port to bind the preview server to (localhost-only).   | Integer | optional |  `8765`  |


<a id="latex_test"></a>

## latex_test

<pre>
load("@rules_latex//latex:defs.bzl", "latex_test")

latex_test(<a href="#latex_test-name">name</a>, <a href="#latex_test-deps">deps</a>, <a href="#latex_test-srcs">srcs</a>, <a href="#latex_test-biber">biber</a>, <a href="#latex_test-biber_strategy">biber_strategy</a>, <a href="#latex_test-cache">cache</a>, <a href="#latex_test-forbidden_patterns">forbidden_patterns</a>,
           <a href="#latex_test-forbidden_patterns_replace">forbidden_patterns_replace</a>, <a href="#latex_test-main">main</a>, <a href="#latex_test-outfmt">outfmt</a>, <a href="#latex_test-pkg_files">pkg_files</a>, <a href="#latex_test-required_patterns">required_patterns</a>)
</pre>

Compiles a LaTeX document and asserts on the resulting log.

**ATTRIBUTES**


| Name  | Description | Type | Mandatory | Default |
| :------------- | :------------- | :------------- | :------------- | :------------- |
| <a id="latex_test-name"></a>name |  A unique name for this target.   | <a href="https://bazel.build/concepts/labels#target-names">Name</a> | required |  |
| <a id="latex_test-deps"></a>deps |  Other targets that contribute LaTeX sources (typically `latex_library` or `latex_pkg`).   | <a href="https://bazel.build/concepts/labels">List of labels</a> | optional |  `[]`  |
| <a id="latex_test-srcs"></a>srcs |  All LaTeX source files needed to compile the document.   | <a href="https://bazel.build/concepts/labels">List of labels</a> | required |  |
| <a id="latex_test-biber"></a>biber |  Enable biber bibliography processing for the test compile, mirroring the same-named attribute on latex_document. When True, the toolchain biber binary is staged onto PATH so tectonic's biblatex subprocess can resolve it.   | Boolean | optional |  `False`  |
| <a id="latex_test-biber_strategy"></a>biber_strategy |  Which biber binary to use when `biber = True`. `"toolchain"` (default) uses the rules_latex-vendored biber; `"system"` uses whatever biber is on $PATH when the test runs.   | String | optional |  `"toolchain"`  |
| <a id="latex_test-cache"></a>cache |  Optional cache snapshot tarball (typically produced by `latex_cache_snapshot`). When set, the test extracts the snapshot and runs tectonic with `--only-cached`, giving a fully offline test that doesn't need internet to run. Takes precedence over the toolchain-level bundle.   | <a href="https://bazel.build/concepts/labels">Label</a> | optional |  `None`  |
| <a id="latex_test-forbidden_patterns"></a>forbidden_patterns |  Substrings whose presence in the tectonic log file fails the test. Appended to a sensible default list (LaTeX Error, Undefined control sequence, Emergency stop, Fatal error). Set `forbidden_patterns_replace = True` to discard the defaults entirely.   | List of strings | optional |  `[]`  |
| <a id="latex_test-forbidden_patterns_replace"></a>forbidden_patterns_replace |  If True, `forbidden_patterns` replaces the default list instead of extending it.   | Boolean | optional |  `False`  |
| <a id="latex_test-main"></a>main |  The top-level .tex file passed to tectonic. Must also appear in `srcs`.   | <a href="https://bazel.build/concepts/labels">Label</a> | required |  |
| <a id="latex_test-outfmt"></a>outfmt |  Output format. Passed to tectonic's --outfmt.   | String | optional |  `"pdf"`  |
| <a id="latex_test-pkg_files"></a>pkg_files |  Same semantics as `latex_document.pkg_files`. Override the staged path of specific inputs.   | <a href="https://bazel.build/rules/lib/core/dict">Dictionary: Label -> String</a> | optional |  `{}`  |
| <a id="latex_test-required_patterns"></a>required_patterns |  Substrings that MUST appear in the tectonic log file. Useful for asserting a particular package was loaded or a specific shipout happened.   | List of strings | optional |  `[]`  |


<a id="LatexInfo"></a>

## LatexInfo

<pre>
load("@rules_latex//latex:defs.bzl", "LatexInfo")

LatexInfo(<a href="#LatexInfo-srcs">srcs</a>, <a href="#LatexInfo-search_paths">search_paths</a>, <a href="#LatexInfo-offline_strategy">offline_strategy</a>)
</pre>

Information about a LaTeX source set or compiled document.

**FIELDS**

| Name  | Description |
| :------------- | :------------- |
| <a id="LatexInfo-srcs"></a>srcs |  depset[File]: transitive set of LaTeX source files (.tex, .sty, .cls, .bib, images, etc.) that documents depending on this target need to see.    |
| <a id="LatexInfo-search_paths"></a>search_paths |  depset[string]: directories (relative to the Bazel execroot) that downstream tectonic invocations should add to TEXINPUTS/BIBINPUTS/BSTINPUTS.    |
| <a id="LatexInfo-offline_strategy"></a>offline_strategy |  string: which offline-mode strategy the target resolved to. One of "user_cache" (explicit `cache = "..."` attr), "bundle" (toolchain-level tectonic.bundle()), or "implicit" (implicit populate-cache pipeline). Set only by `latex_document`; other rules that provide `LatexInfo` (`latex_library`, `latex_pkg`) leave it as the empty string. Consumed by `latex_serve_web` to decide whether to interpose a persistent serve-time cache snapshot via the `//latex:_serve_cache_override` build setting.    |


