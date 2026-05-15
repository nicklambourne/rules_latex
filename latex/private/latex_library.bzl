"""The `latex_library` rule.

A `latex_library` is a logical grouping of related LaTeX sources (style files,
class files, shared preamble, helper macros) that one or more
`latex_document`s depend on. It produces no PDF output; it simply propagates
its sources via `LatexInfo` for downstream rules to assemble.
"""

load("//latex:providers.bzl", "LatexInfo")

def _latex_library_impl(ctx):
    transitive_srcs = []
    transitive_paths = []
    for dep in ctx.attr.deps:
        if LatexInfo in dep:
            transitive_srcs.append(dep[LatexInfo].srcs)
            transitive_paths.append(dep[LatexInfo].search_paths)

    return [
        DefaultInfo(files = depset(direct = ctx.files.srcs)),
        LatexInfo(
            srcs = depset(
                direct = ctx.files.srcs,
                transitive = transitive_srcs,
            ),
            search_paths = depset(
                direct = [ctx.label.package] if ctx.label.package else [],
                transitive = transitive_paths,
            ),
        ),
    ]

latex_library = rule(
    implementation = _latex_library_impl,
    doc = "A reusable collection of LaTeX source files.",
    attrs = {
        "srcs": attr.label_list(
            doc = "LaTeX source files (.tex/.sty/.cls/etc.) exposed by this library.",
            allow_files = True,
            mandatory = True,
        ),
        "deps": attr.label_list(
            doc = "Other latex_library / latex_pkg targets this library depends on.",
            providers = [[LatexInfo]],
        ),
    },
    provides = [LatexInfo],
)
