<!-- Generated with Stardoc: http://skydoc.bazel.build -->

Providers exposed by rules_latex.

`LatexInfo` propagates the transitive set of LaTeX source files that a target
contributes, plus any options that downstream documents should inherit.

`LatexDocumentInfo` carries the compile-time inputs (main file, biber binary,
pkg_files overrides) of a `latex_document` target, so consumers like
`latex_serve_web` can drive a parallel cache-priming invocation without
re-introspecting attributes.


## Providers

- [LatexDocumentInfo](#LatexDocumentInfo)
- [LatexInfo](#LatexInfo)


<a id="LatexDocumentInfo"></a>

## LatexDocumentInfo

<pre>
load("@rules_latex//latex:providers.bzl", "LatexDocumentInfo")

LatexDocumentInfo(<a href="#LatexDocumentInfo-main">main</a>, <a href="#LatexDocumentInfo-tectonic">tectonic</a>, <a href="#LatexDocumentInfo-biber">biber</a>, <a href="#LatexDocumentInfo-use_system_biber">use_system_biber</a>, <a href="#LatexDocumentInfo-pkg_files">pkg_files</a>, <a href="#LatexDocumentInfo-populate_tool">populate_tool</a>, <a href="#LatexDocumentInfo-staging_lib">staging_lib</a>)
</pre>

Compile-time inputs of a `latex_document` target. Exposed so live-preview rules can drive their own parallel tectonic invocations (in particular, a serve-startup cache prime) without re-introspecting the document's attributes.

**FIELDS**

| Name  | Description |
| :------------- | :------------- |
| <a id="LatexDocumentInfo-main"></a>main |  File: the main .tex file passed to tectonic.    |
| <a id="LatexDocumentInfo-tectonic"></a>tectonic |  File: the tectonic binary resolved from the toolchain.    |
| <a id="LatexDocumentInfo-biber"></a>biber |  File or None: the biber binary, if biber = True was set.    |
| <a id="LatexDocumentInfo-use_system_biber"></a>use_system_biber |  bool: True when biber_strategy = "system".    |
| <a id="LatexDocumentInfo-pkg_files"></a>pkg_files |  list[(File, string)]: explicit staging overrides.    |
| <a id="LatexDocumentInfo-populate_tool"></a>populate_tool |  File: the tools/tectonic_populate_cache.py script.    |
| <a id="LatexDocumentInfo-staging_lib"></a>staging_lib |  File: the tools/staging.py library imported by populate_tool.    |


<a id="LatexInfo"></a>

## LatexInfo

<pre>
load("@rules_latex//latex:providers.bzl", "LatexInfo")

LatexInfo(<a href="#LatexInfo-srcs">srcs</a>, <a href="#LatexInfo-search_paths">search_paths</a>, <a href="#LatexInfo-offline_strategy">offline_strategy</a>)
</pre>

Information about a LaTeX source set or compiled document.

**FIELDS**

| Name  | Description |
| :------------- | :------------- |
| <a id="LatexInfo-srcs"></a>srcs |  depset[File]: transitive set of LaTeX source files (.tex, .sty, .cls, .bib, images, etc.) that documents depending on this target need to see.    |
| <a id="LatexInfo-search_paths"></a>search_paths |  depset[string]: directories (relative to the Bazel execroot) that downstream tectonic invocations should add to TEXINPUTS/BIBINPUTS/BSTINPUTS.    |
| <a id="LatexInfo-offline_strategy"></a>offline_strategy |  string: which offline-mode strategy the target resolved to. One of "user_cache" (explicit `cache = "..."` attr), "bundle" (toolchain-level tectonic.bundle()), or "implicit" (implicit populate-cache pipeline). Set only by `latex_document`; other rules that provide `LatexInfo` (`latex_library`, `latex_pkg`) leave it as the empty string. Consumed by `latex_serve_web` to decide whether to interpose a persistent serve-time cache snapshot via the `//latex:_serve_cache_override` build setting.    |


