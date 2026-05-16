<!-- Generated with Stardoc: http://skydoc.bazel.build -->

Providers exposed by rules_latex.

`LatexInfo` propagates the transitive set of LaTeX source files that a target
contributes, plus any options that downstream documents should inherit.


## Providers

- [LatexInfo](#LatexInfo)


<a id="LatexInfo"></a>

## LatexInfo

<pre>
load("@rules_latex//latex:providers.bzl", "LatexInfo")

LatexInfo(<a href="#LatexInfo-srcs">srcs</a>, <a href="#LatexInfo-search_paths">search_paths</a>)
</pre>

Information about a LaTeX source set or compiled document.

**FIELDS**

| Name  | Description |
| :------------- | :------------- |
| <a id="LatexInfo-srcs"></a>srcs |  depset[File]: transitive set of LaTeX source files (.tex, .sty, .cls, .bib, images, etc.) that documents depending on this target need to see.    |
| <a id="LatexInfo-search_paths"></a>search_paths |  depset[string]: directories (relative to the Bazel execroot) that downstream tectonic invocations should add to TEXINPUTS/BIBINPUTS/BSTINPUTS.    |


