"""The `latex_test` rule.

`latex_test` is a Bazel test that compiles a LaTeX document with tectonic and
asserts on the resulting log file. It is useful as a regression test for
documents you care about: catch the day a newly-added \\cite{} stops
resolving, an overfull hbox sneaks in, or a package starts emitting a
deprecation warning.

The test logic itself is a small shell wrapper that:

1. Invokes the same `tectonic_compile.py` tool that `latex_document` uses,
   so source-staging behaviour is identical to a real build (including the
   main-rooted layout introduced in v0.3).
2. Greps the produced `.log` for patterns drawn from the rule's attributes.
3. Exits non-zero if any patterns are matched (failure) or required patterns
   are missing (also failure).

The set of "bad" patterns defaults to undefined references, missing fonts,
and runtime errors — the things that should never silently slip into a
build.
"""

load("//latex:providers.bzl", "LatexInfo")

# Patterns that, if present in the log, fail the test by default. Users can
# add to this list via `forbidden_patterns` or override entirely with
# `forbidden_patterns_replace = True` (handled below).
_DEFAULT_FORBIDDEN_PATTERNS = [
    "LaTeX Error:",
    "! Undefined control sequence",
    "Emergency stop",
    "Fatal error occurred",
]

def _collect_transitive_srcs(deps):
    return [dep[LatexInfo].srcs for dep in deps if LatexInfo in dep]

def _resolved_pkg_files(ctx):
    out = []
    for label, rel in ctx.attr.pkg_files.items():
        files = label.files.to_list()
        if len(files) != 1:
            fail(
                "pkg_files key {} expands to {} files; expected exactly one."
                    .format(label, len(files)),
            )
        out.append((files[0], rel))
    return out

def _latex_test_impl(ctx):
    main = ctx.file.main
    if main not in ctx.files.srcs:
        fail("`main` ({}) must also appear in `srcs`.".format(main.short_path))

    all_srcs = depset(
        direct = ctx.files.srcs,
        transitive = _collect_transitive_srcs(ctx.attr.deps),
    )

    toolchain = ctx.toolchains["//latex/toolchain:toolchain_type"].latex_toolchain_info
    tectonic = toolchain.tectonic

    # Resolve biber. Same logic as latex_document's _resolve_biber but
    # inlined since this rule is otherwise self-contained.
    biber_file = None
    use_system_biber = False
    if ctx.attr.biber:
        if ctx.attr.biber_strategy == "system":
            use_system_biber = True
        else:
            if toolchain.biber == None:
                fail(
                    ("latex_test(biber = True) on {}, but the resolved " +
                     "toolchain has no biber binary. See DESIGN.md §4.9 " +
                     "for the linux/aarch64 workaround.").format(ctx.label),
                )
            biber_file = toolchain.biber

    pkg_files = _resolved_pkg_files(ctx)

    forbidden = list(_DEFAULT_FORBIDDEN_PATTERNS) if not ctx.attr.forbidden_patterns_replace else []
    forbidden.extend(ctx.attr.forbidden_patterns)
    required = list(ctx.attr.required_patterns)

    test_script = ctx.actions.declare_file(ctx.label.name + ".sh")

    # Cache strategy: per-document snapshot > toolchain bundle > implicit
    # pipeline. The implicit pipeline path expects an implicit-cache
    # tarball produced by `latex_document` to also live in runfiles —
    # but a latex_test target doesn't have access to a sibling
    # latex_document's intermediate outputs without an explicit
    # dependency. So when neither cache nor bundle is set we run a
    # one-shot online tectonic invocation (the implicit pipeline's
    # PopulateCache step, inlined).
    #
    # This is consistent with latex_document's pre-v0.2 behaviour:
    # without `cache` or a toolchain bundle, the first build is online
    # and Bazel's action cache reuses the result. For tests this means
    # `bazel test` may need network the first time, then runs hermetic.
    cache_snapshot = ctx.file.cache
    cache_args = ""
    if cache_snapshot:
        cache_args = '--cache-tarball "{}"'.format(cache_snapshot.short_path)
    elif toolchain.bundle:
        cache_args = '--bundle "{}"'.format(toolchain.bundle.short_path)
    else:
        # No cache, no bundle: drive the populate-cache wrapper inline
        # to create a one-shot cache, then feed it to the compile tool.
        # `latex_document`'s implicit pipeline does this at action time;
        # here we do it at test time.
        cache_args = "__IMPLICIT__"

    biber_arg = (
        '--biber "{}"'.format(biber_file.short_path) if biber_file else ""
    )

    src_args = " \\\n    ".join([
        '--src "{}"'.format(s.short_path)
        for s in all_srcs.to_list()
    ])
    pkg_file_args = " \\\n    ".join([
        '--pkg-file "{src}={rel}"'.format(
            src = f.short_path,
            rel = rel,
        )
        for (f, rel) in pkg_files
    ])

    script_prefix = """\
#!/usr/bin/env bash
set -euo pipefail

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

PYTHON="${{PYTHON:-python3}}"
"""

    if cache_args == "__IMPLICIT__":
        compile_cache_args = '--cache-tarball "$WORK/cache.tar.gz"'
        implicit_prime = """\
# No cache or bundle supplied: prime one online, then compile against
# it. Same logic as latex_document's implicit pipeline, but inlined
# here because tests can't depend on a sibling latex_document's
# intermediate cache output.
"$PYTHON" "{populate_tool}" \\
    --tectonic "{tectonic}" \\
    --main "{main}" \\
    --output "$WORK/cache.tar.gz" \\
    {biber_arg} \\
    {src_args} \\
    {pkg_file_args}

""".format(
            populate_tool = ctx.file._populate_tool.short_path,
            tectonic = tectonic.short_path,
            main = main.short_path,
            biber_arg = biber_arg,
            src_args = src_args,
            pkg_file_args = pkg_file_args,
        )
    else:
        compile_cache_args = cache_args
        implicit_prime = ""

    script = script_prefix + implicit_prime + """\
"$PYTHON" "{tool}" \\
    --tectonic "{tectonic}" \\
    --main "{main}" \\
    --outfmt {outfmt} \\
    --output "$WORK/output.{outfmt}" \\
    --log-output "$WORK/output.log" \\
    {compile_cache_args} \\
    {biber_arg} \\
    {src_args} \\
    {pkg_file_args}

LOG="$WORK/output.log"
if [[ ! -f "$LOG" ]]; then
    echo "FAIL: tectonic_compile.py did not produce a log file" >&2
    exit 1
fi

status=0
{forbidden_checks}
{required_checks}
exit $status
""".format(
        tool = ctx.file._compile_tool.short_path,
        tectonic = tectonic.short_path,
        main = main.short_path,
        outfmt = ctx.attr.outfmt,
        compile_cache_args = compile_cache_args,
        biber_arg = biber_arg,
        src_args = src_args,
        pkg_file_args = pkg_file_args,
        forbidden_checks = "\n".join([
            'if grep -F -e {pat} "$LOG" >/dev/null; then\n'.format(pat = repr(p)) +
            '    echo "FAIL: forbidden pattern found in log: {pat}" >&2\n'.format(pat = p) +
            "    status=1\nfi"
            for p in forbidden
        ]),
        required_checks = "\n".join([
            'if ! grep -F -e {pat} "$LOG" >/dev/null; then\n'.format(pat = repr(p)) +
            '    echo "FAIL: required pattern not found in log: {pat}" >&2\n'.format(pat = p) +
            "    status=1\nfi"
            for p in required
        ]),
    )
    ctx.actions.write(test_script, script, is_executable = True)

    runfiles_files = (
        [main, tectonic, ctx.file._compile_tool, ctx.file._populate_tool, ctx.file._staging_lib] +
        ([toolchain.bundle] if toolchain.bundle and not cache_snapshot else []) +
        ([cache_snapshot] if cache_snapshot else []) +
        ([biber_file] if biber_file else []) +
        [f for (f, _) in pkg_files]
    )
    runfiles = ctx.runfiles(
        files = runfiles_files,
        transitive_files = all_srcs,
    )
    return [DefaultInfo(executable = test_script, runfiles = runfiles)]

latex_test = rule(
    implementation = _latex_test_impl,
    doc = "Compiles a LaTeX document and asserts on the resulting log.",
    test = True,
    attrs = {
        "main": attr.label(
            doc = "The top-level .tex file passed to tectonic. Must also " +
                  "appear in `srcs`.",
            allow_single_file = [".tex"],
            mandatory = True,
        ),
        "srcs": attr.label_list(
            doc = "All LaTeX source files needed to compile the document.",
            allow_files = True,
            mandatory = True,
        ),
        "deps": attr.label_list(
            doc = "Other targets that contribute LaTeX sources " +
                  "(typically `latex_library` or `latex_pkg`).",
            providers = [[LatexInfo]],
        ),
        "outfmt": attr.string(
            doc = "Output format. Passed to tectonic's --outfmt.",
            default = "pdf",
            values = ["pdf", "html", "xdv", "aux"],
        ),
        "cache": attr.label(
            doc = "Optional cache snapshot tarball (typically produced by " +
                  "`latex_cache_snapshot`). When set, the test extracts the " +
                  "snapshot and runs tectonic with `--only-cached`, giving " +
                  "a fully offline test that doesn't need internet to run. " +
                  "Takes precedence over the toolchain-level bundle.",
            allow_single_file = [".tar.gz", ".tgz"],
        ),
        "biber": attr.bool(
            doc = "Enable biber bibliography processing for the test " +
                  "compile, mirroring the same-named attribute on " +
                  "latex_document. When True, the toolchain biber binary " +
                  "is staged onto PATH so tectonic's biblatex subprocess " +
                  "can resolve it.",
            default = False,
        ),
        "biber_strategy": attr.string(
            doc = "Which biber binary to use when `biber = True`. " +
                  "`\"toolchain\"` (default) uses the rules_latex-vendored " +
                  "biber; `\"system\"` uses whatever biber is on $PATH " +
                  "when the test runs.",
            default = "toolchain",
            values = ["toolchain", "system"],
        ),
        "pkg_files": attr.label_keyed_string_dict(
            doc = "Same semantics as `latex_document.pkg_files`. Override " +
                  "the staged path of specific inputs.",
            allow_files = True,
        ),
        "forbidden_patterns": attr.string_list(
            doc = "Substrings whose presence in the tectonic log file " +
                  "fails the test. Appended to a sensible default list " +
                  "(LaTeX Error, Undefined control sequence, Emergency " +
                  "stop, Fatal error). Set `forbidden_patterns_replace = " +
                  "True` to discard the defaults entirely.",
        ),
        "forbidden_patterns_replace": attr.bool(
            doc = "If True, `forbidden_patterns` replaces the default list " +
                  "instead of extending it.",
            default = False,
        ),
        "required_patterns": attr.string_list(
            doc = "Substrings that MUST appear in the tectonic log file. " +
                  "Useful for asserting a particular package was loaded " +
                  "or a specific shipout happened.",
        ),
        "_compile_tool": attr.label(
            default = "//tools:tectonic_compile.py",
            allow_single_file = True,
        ),
        "_populate_tool": attr.label(
            default = "//tools:tectonic_populate_cache.py",
            allow_single_file = True,
        ),
        "_staging_lib": attr.label(
            default = "//tools:staging.py",
            allow_single_file = True,
        ),
    },
    toolchains = ["//latex/toolchain:toolchain_type"],
)
