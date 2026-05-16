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
snapshot — no internet, no 3 GB full bundle.

The rule is *not* a normal build action because it inherently needs
network access on first invocation and a writable source-tree
destination. It's a developer command, run on demand, much like
`cargo vendor` or `pip-compile`.
"""

load("//latex:providers.bzl", "LatexInfo")

def _collect_transitive_srcs(deps):
    return [dep[LatexInfo].srcs for dep in deps if LatexInfo in dep]

def _latex_cache_snapshot_impl(ctx):
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
                "resolved toolchain has no biber binary. See DESIGN.md " +
                "§4.9 for the linux/aarch64 workaround.".format(ctx.label),
            )
        biber_file = toolchain.biber

    launcher = ctx.actions.declare_file(ctx.label.name + ".sh")

    src_args = " \\\n        ".join([
        '--src "$RUNFILES/{}"'.format(s.short_path)
        for s in all_srcs
    ])
    biber_arg = (
        '--biber "$RUNFILES/{}"'.format(biber_file.short_path) if biber_file else ""
    )

    script = """\
#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${{BUILD_WORKSPACE_DIRECTORY:-}}" ]]; then
    echo "ERROR: this target must be invoked with 'bazel run', not 'bazel build'." >&2
    echo "  bazel run //{pkg}:{name}" >&2
    exit 1
fi

RUNFILES="$(pwd)"
PYTHON="${{PYTHON:-python3}}"

# We deliberately don't pass --src-root: the tool computes the
# deepest common ancestor of (main + all srcs), which handles both
# single-package documents and cross-package latex_pkg deps.
exec "$PYTHON" "$RUNFILES/{tool}" \\
    --tectonic "$RUNFILES/{tectonic}" \\
    --main "$RUNFILES/{main}" \\
    {src_args} \\
    --workspace "$BUILD_WORKSPACE_DIRECTORY" \\
    --output "{output}" \\
    {biber_arg}
""".format(
        pkg = ctx.label.package,
        name = ctx.label.name,
        tool = ctx.file._tool.short_path,
        tectonic = tectonic.short_path,
        main = main.short_path,
        src_args = src_args,
        output = ctx.attr.output,
        biber_arg = biber_arg,
    )
    ctx.actions.write(launcher, script, is_executable = True)

    runfiles_files = [tectonic, ctx.file._tool] + all_srcs
    if biber_file:
        runfiles_files.append(biber_file)
    runfiles = ctx.runfiles(files = runfiles_files)
    return [DefaultInfo(executable = launcher, runfiles = runfiles)]

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
        "_tool": attr.label(
            default = "//tools:make_cache_snapshot.py",
            allow_single_file = True,
        ),
    },
    toolchains = ["//latex/toolchain:toolchain_type"],
)
