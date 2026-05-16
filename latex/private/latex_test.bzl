"""The `latex_test` rule.

`latex_test` is a Bazel test that compiles a LaTeX document with tectonic and
asserts on the resulting log file. It is useful as a regression test for
documents you care about: catch the day a newly-added \\cite{} stops
resolving, an overfull hbox sneaks in, or a package starts emitting a
deprecation warning.

The test logic itself is a small shell wrapper that:

1. Invokes tectonic exactly the same way `latex_document` would.
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

    forbidden = list(_DEFAULT_FORBIDDEN_PATTERNS) if not ctx.attr.forbidden_patterns_replace else []
    forbidden.extend(ctx.attr.forbidden_patterns)
    required = list(ctx.attr.required_patterns)

    test_script = ctx.actions.declare_file(ctx.label.name + ".sh")

    # The test driver runs tectonic and then checks the log. We embed the
    # tectonic invocation directly rather than depending on a separate
    # binary so the test stays self-contained.
    #
    # Three offline-mode variants share the action: a per-document cache
    # snapshot (preferred when set), a toolchain-wide bundle, or pure
    # online mode.
    cache_setup = ""
    bundle_args = ""
    cache_snapshot = ctx.file.cache
    if cache_snapshot:
        cache_setup = 'tar -xzf "$(pwd)/{}" -C "$WORK/cache"\n'.format(
            cache_snapshot.short_path,
        )
        bundle_args = "--only-cached "
    elif toolchain.bundle:
        bundle_args = '--bundle "$(pwd)/{}" --only-cached '.format(
            toolchain.bundle.short_path,
        )

    # Stage biber onto PATH inside the test's mktemp work dir so
    # tectonic's biblatex subprocess finds `biber` by basename. Mirror
    # the approach used inside latex_document.
    biber_setup = ""
    if biber_file:
        biber_setup = (
            'mkdir -p "$WORK/bin"\n' +
            'ln -s "$(pwd)/{}" "$WORK/bin/biber"\n'.format(biber_file.short_path) +
            'export PATH="$WORK/bin:${PATH:-/usr/bin:/bin}"\n'
        )
    elif use_system_biber:
        # Whatever PATH the user runs the test with is propagated by
        # `bazel test` by default; nothing to do here.
        pass

    script = """\
#!/usr/bin/env bash
set -euo pipefail

TECTONIC="$(pwd)/{tectonic}"
MAIN="$(pwd)/{main}"
WORK="$(mktemp -d)"
mkdir -p "$WORK/cache"
trap 'rm -rf "$WORK"' EXIT
{biber_setup}{cache_setup}
TECTONIC_CACHE_DIR="$WORK/cache" \\
LC_ALL=C.UTF-8 \\
"$TECTONIC" -X compile --outfmt {outfmt} --outdir "$WORK" --keep-logs {bundle_args}"$MAIN"

LOG="$WORK/{logname}"
if [[ ! -f "$LOG" ]]; then
    echo "FAIL: tectonic did not produce a log file at $LOG" >&2
    exit 1
fi

status=0
{forbidden_checks}
{required_checks}
exit $status
""".format(
        tectonic = tectonic.short_path,
        main = main.short_path,
        outfmt = ctx.attr.outfmt,
        logname = main.basename[:-len(".tex")] + ".log",
        biber_setup = biber_setup,
        cache_setup = cache_setup,
        bundle_args = bundle_args,
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

    runfiles = ctx.runfiles(
        files = (
            [main, tectonic] +
            ([toolchain.bundle] if toolchain.bundle and not cache_snapshot else []) +
            ([cache_snapshot] if cache_snapshot else []) +
            ([biber_file] if biber_file else [])
        ),
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
    },
    toolchains = ["//latex/toolchain:toolchain_type"],
)
