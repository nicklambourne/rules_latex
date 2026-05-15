"""Providers exposed by rules_latex.

`LatexInfo` propagates the transitive set of LaTeX source files that a target
contributes, plus any options that downstream documents should inherit.
"""

LatexInfo = provider(
    doc = "Information about a LaTeX source set or compiled document.",
    fields = {
        "srcs": "depset[File]: transitive set of LaTeX source files (.tex, " +
                ".sty, .cls, .bib, images, etc.) that documents depending on " +
                "this target need to see.",
        "search_paths": "depset[string]: directories (relative to the Bazel " +
                        "execroot) that downstream tectonic invocations " +
                        "should add to TEXINPUTS/BIBINPUTS/BSTINPUTS.",
    },
)
