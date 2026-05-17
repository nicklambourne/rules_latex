"""The `latex_pkg` rule.

`latex_pkg` is conceptually a leaf bundle of resources — typically images,
fonts, bibliographies, or other data files — that documents may need to
reference but that aren't themselves LaTeX source. It is functionally similar
to `latex_library` but is intended to make the distinction visible at the
call site:

    latex_library(name = "macros", srcs = ["preamble.tex", "mymacros.sty"])
    latex_pkg(name = "figures", srcs = glob(["figures/*.png"]))
"""

load("//latex:providers.bzl", "LatexInfo")

def _latex_pkg_impl(ctx):
    return [
        DefaultInfo(files = depset(direct = ctx.files.srcs)),
        LatexInfo(
            srcs = depset(direct = ctx.files.srcs),
            search_paths = depset(direct = [ctx.label.package] if ctx.label.package else []),
            # latex_pkg doesn't compile anything itself, so it has no
            # offline-mode strategy of its own. Consumers that need
            # one should read it from a downstream latex_document.
            offline_strategy = "",
        ),
    ]

latex_pkg = rule(
    implementation = _latex_pkg_impl,
    doc = "A bundle of resource files (images, bib, fonts) consumed by documents.",
    attrs = {
        "srcs": attr.label_list(
            doc = "Resource files exposed by this package.",
            allow_files = True,
            mandatory = True,
        ),
    },
    provides = [LatexInfo],
)
