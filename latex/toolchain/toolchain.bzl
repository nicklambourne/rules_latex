"""The `latex_toolchain` rule.

A `latex_toolchain` packages everything an action needs to invoke tectonic:
the binary itself, optionally a pre-fetched package bundle, and optionally
a biber binary for bibliography processing.

Toolchains of this type are registered automatically by the `tectonic` module
extension defined in `//latex/toolchain:extensions.bzl`.
"""

LatexToolchainInfo = provider(
    doc = "Resolved tectonic toolchain.",
    fields = {
        "tectonic": "File: the tectonic executable.",
        "tectonic_tool": "FilesToRunProvider: executable plus its runtime files.",
        "tectonic_runfiles": "Runfiles: runtime files for launchers.",
        "bundle": "File|None: a fully downloaded offline package bundle, " +
                  "or None for range-fetched/implicit-cache operation.",
        "biber": "File|None: a biber executable for bibliography processing, " +
                 "or None if biber isn't available for this platform.",
        "biber_tool": "FilesToRunProvider|None: biber plus its runtime files.",
        "biber_runfiles": "Runfiles|None: runtime files for biber launchers.",
    },
)

def _latex_toolchain_impl(ctx):
    toolchain_info = platform_common.ToolchainInfo(
        latex_toolchain_info = LatexToolchainInfo(
            tectonic = ctx.executable.tectonic,
            tectonic_tool = ctx.attr.tectonic[DefaultInfo].files_to_run,
            tectonic_runfiles = ctx.attr.tectonic[DefaultInfo].default_runfiles,
            bundle = ctx.file.bundle,
            biber = ctx.executable.biber,
            biber_tool = ctx.attr.biber[DefaultInfo].files_to_run if ctx.attr.biber else None,
            biber_runfiles = ctx.attr.biber[DefaultInfo].default_runfiles if ctx.attr.biber else None,
        ),
    )
    return [toolchain_info]

latex_toolchain = rule(
    implementation = _latex_toolchain_impl,
    doc = "Defines a tectonic-based LaTeX toolchain.",
    attrs = {
        "tectonic": attr.label(
            doc = "The tectonic executable.",
            allow_files = True,
            executable = True,
            cfg = "exec",
            mandatory = True,
        ),
        "bundle": attr.label(
            doc = "Optional fully downloaded package bundle (.ttb or legacy " +
                  ".tar). When set, the toolchain runs tectonic with " +
                  "`--bundle` pointed at this file, making compilation fully " +
                  "hermetic.",
            allow_single_file = [".ttb", ".tar"],
        ),
        "biber": attr.label(
            doc = "Optional biber executable. When set, latex_document " +
                  "actions invoked with `biber = True` make this binary " +
                  "available on PATH so tectonic can shell out to it for " +
                  "bibliography processing. Vendored for every supported " +
                  "platform (biber 2.21).",
            allow_files = True,
            executable = True,
            cfg = "exec",
        ),
    },
)
