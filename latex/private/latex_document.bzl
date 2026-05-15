"""The `latex_document` rule.

Compiles a LaTeX source tree into a PDF (or other tectonic-supported format)
using the resolved tectonic toolchain. The rule transitively collects sources
from any `deps` that provide LatexInfo (i.e. `latex_library` / `latex_pkg`).
"""

load("//latex:providers.bzl", "LatexInfo")

_OUTFMTS = ["pdf", "html", "xdv", "aux"]

def _collect_transitive_srcs(deps):
    return [dep[LatexInfo].srcs for dep in deps if LatexInfo in dep]

def _latex_document_impl(ctx):
    main = ctx.file.main
    if main not in ctx.files.srcs:
        fail("`main` ({}) must also appear in `srcs`.".format(main.short_path))

    all_srcs = depset(
        direct = ctx.files.srcs,
        transitive = _collect_transitive_srcs(ctx.attr.deps),
    )

    outfmt = ctx.attr.outfmt
    output = ctx.actions.declare_file("{}.{}".format(ctx.label.name, outfmt))

    toolchain = ctx.toolchains["//latex/toolchain:toolchain_type"].latex_toolchain_info
    tectonic = toolchain.tectonic

    args = ctx.actions.args()
    args.add("-X")
    args.add("compile")
    args.add("--outfmt", outfmt)
    args.add("--outdir", output.dirname)
    args.add("--keep-logs")
    if toolchain.bundle:
        # Offline mode: tectonic reads packages from the pinned bundle and
        # does not touch the network.
        args.add("--bundle", toolchain.bundle.path)
        args.add("--only-cached")
    for extra in ctx.attr.tectonic_args:
        args.add(extra)
    args.add(main.path)

    inputs = depset(
        direct = [main] + ([toolchain.bundle] if toolchain.bundle else []),
        transitive = [all_srcs],
    )

    ctx.actions.run(
        executable = tectonic,
        arguments = [args],
        inputs = inputs,
        outputs = [output],
        mnemonic = "TectonicCompile",
        progress_message = "Compiling LaTeX %{label}",
        env = {
            # Some downstream tools (e.g. biber) require a UTF-8 locale.
            "LC_ALL": "C.UTF-8",
        },
    )

    return [
        DefaultInfo(files = depset([output])),
        OutputGroupInfo(
            pdf = depset([output]) if outfmt == "pdf" else depset(),
        ),
    ]

latex_document = rule(
    implementation = _latex_document_impl,
    doc = "Compiles a LaTeX source tree using tectonic.",
    attrs = {
        "main": attr.label(
            doc = "The top-level .tex file passed to tectonic. Must also " +
                  "appear in `srcs`.",
            allow_single_file = [".tex"],
            mandatory = True,
        ),
        "srcs": attr.label_list(
            doc = "All LaTeX source files (.tex, .sty, .cls, .bib, images, " +
                  "etc.) that the document compilation might reference.",
            allow_files = True,
            mandatory = True,
        ),
        "deps": attr.label_list(
            doc = "Other targets that contribute LaTeX sources " +
                  "(typically `latex_library` or `latex_pkg`).",
            providers = [[LatexInfo]],
        ),
        "outfmt": attr.string(
            doc = "Output format. Passed to `tectonic -X compile --outfmt`.",
            default = "pdf",
            values = _OUTFMTS,
        ),
        "tectonic_args": attr.string_list(
            doc = "Extra command-line arguments passed to tectonic. Use " +
                  "sparingly; prefer rule-level attributes when possible.",
        ),
    },
    toolchains = ["//latex/toolchain:toolchain_type"],
)
