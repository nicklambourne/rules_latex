"""The `latex_toolchain` rule.

A `latex_toolchain` packages everything an action needs to invoke tectonic:
the binary itself, and (optionally) a pre-fetched offline package bundle.

Toolchains of this type are registered automatically by the `tectonic` module
extension defined in `//latex/toolchain:extensions.bzl`.
"""

LatexToolchainInfo = provider(
    doc = "Resolved tectonic toolchain.",
    fields = {
        "tectonic": "File: the tectonic executable.",
        "bundle": "File|None: an offline package bundle, or None for online " +
                  "(default) operation.",
    },
)

def _latex_toolchain_impl(ctx):
    toolchain_info = platform_common.ToolchainInfo(
        latex_toolchain_info = LatexToolchainInfo(
            tectonic = ctx.file.tectonic,
            bundle = ctx.file.bundle,
        ),
    )
    return [toolchain_info]

latex_toolchain = rule(
    implementation = _latex_toolchain_impl,
    doc = "Defines a tectonic-based LaTeX toolchain.",
    attrs = {
        "tectonic": attr.label(
            doc = "The tectonic executable.",
            allow_single_file = True,
            executable = True,
            cfg = "exec",
            mandatory = True,
        ),
        "bundle": attr.label(
            doc = "Optional offline package bundle (.tar). When set, the " +
                  "toolchain runs tectonic with `--bundle` pointed at this " +
                  "file, making compilation fully hermetic.",
            allow_single_file = [".tar"],
        ),
    },
)
