"""The `latex_cache_snapshot` rule.

`latex_cache_snapshot` produces a small, content-addressed snapshot of
the tectonic cache needed to compile a given document, suitable for
checking into the repository and consuming via `latex_document(cache =
...)`.

Typical workflow:

    latex_document(
        name = "cv",
        main = "cv.tex",
        srcs = ["cv.tex"],
    )

    latex_cache_snapshot(
        name = "cv_cache",
        main = "cv.tex",
        srcs = ["cv.tex"],
        output = "cv_cache.tar.gz",
    )

Then, once, with internet access:

    $ bazel run //:cv_cache

This compiles `cv.tex` once in online mode, captures the resulting
~tens-of-MB tectonic cache, and writes `cv_cache.tar.gz` into the
source tree. After committing the snapshot, `latex_document(cache =
":cv_cache.tar.gz")` builds the document fully offline using only the
snapshot — no internet, no 1.78 GiB full bundle.

The rule is *not* a normal build action because it inherently needs
network access on first invocation and a writable source-tree
destination. It's a developer command, run on demand, much like
`cargo vendor` or `pip-compile`.
"""

load("@bazel_skylib//lib:shell.bzl", "shell")
load("//latex:providers.bzl", "LatexInfo")
load("//latex/private:bundles.bzl", "DEFAULT_BUNDLE")

def _collect_transitive_srcs(deps):
    return [dep[LatexInfo].srcs for dep in deps if LatexInfo in dep]

def _latex_cache_snapshot_impl(ctx):
    if ctx.file.ctan_lock and not ctx.attr.ctan_packages:
        fail("ctan_lock requires ctan_packages on {}".format(ctx.label))
    toolchain = ctx.toolchains["//latex/toolchain:toolchain_type"].latex_toolchain_info
    tectonic = toolchain.tectonic

    main = ctx.file.main
    if main not in ctx.files.srcs:
        fail("`main` ({}) must also appear in `srcs`.".format(main.short_path))

    all_srcs = depset(
        direct = ctx.files.srcs,
        transitive = _collect_transitive_srcs(ctx.attr.deps),
    ).to_list()

    # Decide whether to include biber in the priming run. Snapshots
    # built without biber miss bibliography-related TeX Live files, so
    # users compiling biblatex documents need to opt in here.
    biber_file = None
    if ctx.attr.biber:
        if toolchain.biber == None:
            fail(
                "latex_cache_snapshot(biber = True) on {}, but the " +
                "resolved toolchain has no biber binary. Use a supported " +
                "rules_latex toolchain or provide biber in a custom " +
                "toolchain. See DESIGN.md §4.9.".format(ctx.label),
            )
        biber_file = toolchain.biber

    tool_info = ctx.attr._tool[DefaultInfo]
    tool = tool_info.files_to_run.executable
    launcher = ctx.actions.declare_file(ctx.label.name + ".sh")
    pkg_files = _resolved_pkg_files(ctx)

    src_args = " \\\n        ".join([
        "--src {}".format(shell.quote(s.short_path))
        for s in all_srcs
    ])
    pkg_file_args = " \\\n        ".join([
        "--pkg-file {value}".format(
            value = shell.quote("{}={}".format(src.short_path, rel)),
        )
        for src, rel in pkg_files.items()
    ])
    biber_arg = (
        "--biber {}".format(shell.quote(biber_file.short_path)) if biber_file else ""
    )

    ctan_args = " \\\n    ".join([
        "--ctan-package {}".format(shell.quote(pkg))
        for pkg in ctx.attr.ctan_packages
    ])

    if ctx.file.ctan_lock:
        ctan_args += ' --ctan-lock "{}"'.format(ctx.file.ctan_lock.short_path)

    # Hand the auto-resolver the bundle-resident manifest when
    # ctan_packages is non-empty, so it can filter transitive refs
    # against the bundle and avoid shadowing bundle versions.
    bundle_manifest_arg = (
        "--bundle-manifest {}".format(shell.quote(ctx.file._bundle_manifest.short_path)) if ctx.attr.ctan_packages else ""
    )

    script = """\
#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${{BUILD_WORKSPACE_DIRECTORY:-}}" ]]; then
    echo "ERROR: this target must be invoked with 'bazel run', not 'bazel build'." >&2
    echo {target} >&2
    exit 1
fi

# Bazel sets cwd to the runfiles root for `bazel run`. All file paths
# below are short_path values, which are relative to that root, so
# they resolve correctly without any chdir.
exec {tool} \\
    --tectonic {tectonic} \\
    --main {main} \\
    {src_args} \\
    {pkg_file_args} \\
    {ctan_args} \\
    {bundle_manifest_arg} \\
    --bundle-url {bundle_url} \\
    --workspace "$BUILD_WORKSPACE_DIRECTORY" \\
    --output {output} \\
    {biber_arg}
""".format(
        target = shell.quote("bazel run //{}:{}".format(ctx.label.package, ctx.label.name)),
        tool = shell.quote(tool.short_path),
        tectonic = shell.quote(tectonic.short_path),
        main = shell.quote(main.short_path),
        src_args = src_args,
        pkg_file_args = pkg_file_args,
        ctan_args = ctan_args,
        bundle_manifest_arg = bundle_manifest_arg,
        # Prime against the default bundle (the TL2026 .ttb on R2) so
        # the snapshot's cached format is keyed under that bundle's
        # identity -- matching the --bundle-url the consuming
        # latex_document/latex_test compile passes. See DESIGN §4.10.
        bundle_url = shell.quote(DEFAULT_BUNDLE.url),
        output = shell.quote(ctx.attr.output),
        biber_arg = biber_arg,
    )
    ctx.actions.write(launcher, script, is_executable = True)

    runfiles_files = [tectonic] + all_srcs + list(pkg_files.keys())
    if biber_file:
        runfiles_files.append(biber_file)
    if ctx.attr.ctan_packages:
        runfiles_files.append(ctx.file._bundle_manifest)
    if ctx.file.ctan_lock:
        runfiles_files.append(ctx.file.ctan_lock)
    runfiles = ctx.runfiles(files = runfiles_files).merge(tool_info.default_runfiles)
    runfiles = runfiles.merge(toolchain.tectonic_runfiles)
    if biber_file:
        runfiles = runfiles.merge(toolchain.biber_runfiles)
    return [DefaultInfo(executable = launcher, runfiles = runfiles)]

def _resolved_pkg_files(ctx):
    """Resolve `pkg_files` to a {File: staged-path} dict.

    Each label key must expand to exactly one file (typically a
    `filegroup` with a single src, or a plain file label).
    """
    out = {}
    for label, rel in ctx.attr.pkg_files.items():
        files = label.files.to_list()
        if len(files) != 1:
            fail(
                "pkg_files key {} expands to {} files; expected exactly one."
                    .format(label, len(files)),
            )
        out[files[0]] = rel
    return out

latex_cache_snapshot = rule(
    implementation = _latex_cache_snapshot_impl,
    doc = "Bazel-run target that captures a tectonic cache snapshot.",
    executable = True,
    attrs = {
        "main": attr.label(
            doc = "The top-level .tex file passed to tectonic. Must also " +
                  "appear in `srcs`.",
            allow_single_file = [".tex"],
            mandatory = True,
        ),
        "srcs": attr.label_list(
            doc = "All LaTeX source files needed to compile the document " +
                  "online. The cache snapshot will contain whatever " +
                  "tectonic decides to fetch for this compile, so make " +
                  "sure this list is realistic.",
            allow_files = True,
            mandatory = True,
        ),
        "deps": attr.label_list(
            doc = "Other targets that contribute LaTeX sources.",
            providers = [[LatexInfo]],
        ),
        "output": attr.string(
            doc = "Destination path for the snapshot tarball, relative to " +
                  "the workspace root.",
            mandatory = True,
        ),
        "biber": attr.bool(
            doc = "If True, prime the cache with biber on PATH so the " +
                  "resulting snapshot contains bibliography-related files. " +
                  "Required when consumers compile biblatex documents " +
                  "against this snapshot.",
            default = False,
        ),
        "ctan_packages": attr.string_list(
            doc = "Names of CTAN packages to download and include in the " +
                  "cache snapshot. Each entry is a CTAN package name " +
                  "(e.g. 'fancyhdr'). Packages are downloaded from CTAN " +
                  "mirrors in TDS format during the snapshot generation.",
            default = [],
        ),
        "ctan_lock": attr.label(
            doc = "Optional version-1 JSON lock of CTAN URLs and SHA-256 digests.",
            allow_single_file = True,
        ),
        "pkg_files": attr.label_keyed_string_dict(
            doc = "Map of label-of-input -> staged-relative-path. " +
                  "Overrides the auto-staging path for the listed inputs, " +
                  "letting you place a file anywhere under main.tex's " +
                  "work directory. Typical use: stage a cross-package " +
                  "`.bib` file as a sibling of main.tex so " +
                  "`\\addbibresource{refs.bib}` works without `..` " +
                  "(which tectonic refuses to hand to external tools).",
            allow_files = True,
        ),
        "_tool": attr.label(
            default = "//tools:tectonic_populate_cache",
            executable = True,
            cfg = "target",
        ),
        "_bundle_manifest": attr.label(
            default = "//latex/toolchain:bundle_manifest.txt",
            allow_single_file = True,
        ),
    },
    toolchains = ["//latex/toolchain:toolchain_type"],
)
