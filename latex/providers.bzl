"""Providers exposed by rules_latex.

`LatexInfo` propagates the transitive set of LaTeX source files that a target
contributes, plus any options that downstream documents should inherit.

`LatexDocumentInfo` carries the compile-time inputs (main file, biber binary,
pkg_files overrides) of a `latex_document` target, so consumers like
`latex_serve_web` can drive a parallel cache-priming invocation without
re-introspecting attributes.
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
        "offline_strategy": "string: which offline-mode strategy the target " +
                            "resolved to. One of \"user_cache\" (explicit " +
                            "`cache = \"...\"` attr), \"bundle\" (toolchain-" +
                            "level tectonic.bundle()), or \"implicit\" " +
                            "(implicit populate-cache pipeline). " +
                            "Set only by `latex_document`; other rules " +
                            "that provide `LatexInfo` (`latex_library`, " +
                            "`latex_pkg`) leave it as the empty string. " +
                            "Consumed by `latex_serve_web` to decide " +
                            "whether to interpose a persistent serve-time " +
                            "cache snapshot via the " +
                            "`//latex:_serve_cache_override` build setting.",
    },
)

LatexDocumentInfo = provider(
    doc = "Compile-time inputs of a `latex_document` target. Exposed so " +
          "live-preview rules can drive their own parallel tectonic " +
          "invocations (in particular, a serve-startup cache prime) " +
          "without re-introspecting the document's attributes.",
    fields = {
        "main": "File: the main .tex file passed to tectonic.",
        "tectonic": "File: the tectonic binary resolved from the toolchain.",
        "biber": "File or None: the biber binary, if biber = True was set.",
        "use_system_biber": "bool: True when biber_strategy = \"system\".",
        "pkg_files": "list[(File, string)]: explicit staging overrides.",
        "populate_tool": "File: the tools/tectonic_populate_cache.py script.",
        "staging_lib": "File: the tools/staging.py library imported by " +
                       "populate_tool.",
    },
)
