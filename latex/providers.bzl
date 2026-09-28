"""Providers exposed by rules_latex.

`LatexInfo` propagates the transitive set of LaTeX source files that a target
contributes. Document actions stage those files into the work directory.

`LatexDocumentInfo` carries the compile-time inputs (main file, biber binary,
pkg_files overrides) of a `latex_document` target, so consumers like
`latex_live` can drive a parallel cache-priming invocation without
re-introspecting attributes.
"""

LatexInfo = provider(
    doc = "Information about a LaTeX source set or compiled document.",
    fields = {
        "srcs": "depset[File]: transitive set of LaTeX source files (.tex, " +
                ".sty, .cls, .bib, images, etc.) that documents depending on " +
                "this target need to see.",
        "search_paths": "depset[string]: retained for provider compatibility. " +
                        "Document compilation does not read this field or " +
                        "set TEXINPUTS/BIBINPUTS/BSTINPUTS; it stages " +
                        "source files by path instead. Use pkg_files to " +
                        "override a staged path.",
        "offline_strategy": "string: which offline-mode strategy the target " +
                            "resolved to. One of \"user_cache\" (explicit " +
                            "`cache = \"...\"` attr), \"bundle\" (toolchain-" +
                            "level tectonic.bundle()), or \"implicit\" " +
                            "(implicit populate-cache pipeline). " +
                            "Set only by `latex_document`; other rules " +
                            "that provide `LatexInfo` (`latex_library`, " +
                            "`latex_pkg`) leave it as the empty string. " +
                            "Consumed by `latex_live` to decide " +
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
        "tectonic_runfiles": "Runfiles: runtime files of the tectonic executable.",
        "biber": "File or None: the biber binary, if biber = True was set.",
        "biber_runfiles": "Runfiles or None: runtime files of biber, if enabled.",
        "use_system_biber": "bool: True when biber_strategy = \"system\".",
        "pkg_files": "list[(File, string)]: explicit staging overrides.",
        "populate_tool": "File: the tools/tectonic_populate_cache.py script.",
        "staging_lib": "File: the tools/staging.py library imported by " +
                       "populate_tool.",
        "bundle_url": "string: pinned bundle identity used by the implicit prime.",
        "ctan_packages": "list[string]: CTAN packages requested by the document.",
        "ctan_lock": "File or None: exact URL and SHA-256 lock for CTAN packages.",
        "bundle_manifest": "File: the pinned bundle package manifest for CTAN resolution.",
    },
)
