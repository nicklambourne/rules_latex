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
    if ctx.attr.reproducible and ctx.attr.synctex:
        fail(
            "`reproducible` and `synctex` cannot both be True on the same " +
            "latex_document: tectonic's -Z deterministic-mode disables " +
            "SyncTeX output (auxiliary files would otherwise include " +
            "absolute paths that aren't deterministic across machines).",
        )

    all_srcs = depset(
        direct = ctx.files.srcs,
        transitive = _collect_transitive_srcs(ctx.attr.deps),
    )

    outfmt = ctx.attr.outfmt
    output = ctx.actions.declare_file("{}.{}".format(ctx.label.name, outfmt))

    # Tectonic always names its output after the input file (e.g.
    # `hello.tex` → `hello.pdf`). When the target name differs from the
    # input stem we ask tectonic to write into a private subdir of the
    # action's outputs and then rename the result; otherwise we let
    # tectonic write directly to the declared output's directory.
    main_stem = main.basename[:-len("." + main.extension)] if main.extension else main.basename
    needs_rename = main_stem != ctx.label.name
    work_subdir = output.dirname + "/_" + ctx.label.name if needs_rename else output.dirname

    # Declare the synctex file as an additional output so live-preview
    # tooling can find it next to the PDF. Tectonic writes it as
    # `<stem>.synctex.gz`; if needs_rename, we mv it after compile.
    synctex_output = None
    if ctx.attr.synctex:
        synctex_output = ctx.actions.declare_file(
            "{}.synctex.gz".format(ctx.label.name),
        )

    toolchain = ctx.toolchains["//latex/toolchain:toolchain_type"].latex_toolchain_info
    tectonic = toolchain.tectonic

    # A per-document `cache` snapshot takes precedence over the
    # toolchain-wide bundle, if both are set. It's a much smaller,
    # focused alternative produced by `latex_cache_snapshot`.
    cache_snapshot = ctx.file.cache

    args = ctx.actions.args()
    args.add(tectonic.path)
    args.add("-X")
    args.add("compile")
    args.add("--outfmt", outfmt)
    args.add("--outdir", work_subdir)
    args.add("--keep-logs")
    if cache_snapshot:
        # Offline mode via a pre-populated cache: tectonic reads
        # everything from the cache dir we extract below and refuses
        # network access.
        args.add("--only-cached")
    elif toolchain.bundle:
        # Offline mode via a full bundle: tectonic reads packages from
        # the pinned bundle and does not touch the network.
        args.add("--bundle", toolchain.bundle.path)
        args.add("--only-cached")
    if ctx.attr.reproducible:
        # Tectonic honours SOURCE_DATE_EPOCH for PDF creation timestamps;
        # we pass it via env (below) and also enable the engine's
        # deterministic build mode here. See
        # https://reproducible-builds.org/specs/source-date-epoch/.
        args.add("-Z")
        args.add("deterministic-mode")
    if ctx.attr.synctex:
        # Tectonic writes <stem>.synctex.gz alongside the PDF.
        args.add("--synctex")
    for extra in ctx.attr.tectonic_args:
        args.add(extra)
    args.add(main.path)

    inputs = depset(
        direct = (
            [main, tectonic] +
            ([toolchain.bundle] if toolchain.bundle and not cache_snapshot else []) +
            ([cache_snapshot] if cache_snapshot else [])
        ),
        transitive = [all_srcs],
    )

    # We invoke tectonic from a tiny shell wrapper so we can point its
    # cache directory at a per-action writable scratch dir. Tectonic by
    # default derives the cache from `$XDG_CACHE_HOME` / `$HOME`, both of
    # which are unset under Bazel's Linux sandbox — leading to "Read-only
    # file system (os error 30)" on first use. `mktemp -d` always returns
    # a writable temp dir under `$TMPDIR` (which Bazel guarantees per
    # action), and the directory is reaped with the action sandbox.
    env = {
        # Some downstream tools (e.g. biber) require a UTF-8 locale.
        "LC_ALL": "C.UTF-8",
    }
    if ctx.attr.reproducible:
        # SOURCE_DATE_EPOCH=0 produces fully deterministic PDF metadata
        # (creation/modification dates), and is enough for byte-identical
        # output across runs when combined with -Z deterministic-mode.
        env["SOURCE_DATE_EPOCH"] = "0"

    # When the target name differs from the input stem we have tectonic
    # write to a private subdirectory, then move the result into place.
    # See the comment around `needs_rename` above.
    if needs_rename:
        rename_cmd = 'mkdir -p "{work}" && '.format(work = work_subdir)
        rename_post = ' && mv "{work}/{stem}.{ext}" "{out}"'.format(
            work = work_subdir,
            stem = main_stem,
            ext = outfmt,
            out = output.path,
        )
        if synctex_output:
            rename_post += ' && mv "{work}/{stem}.synctex.gz" "{out}"'.format(
                work = work_subdir,
                stem = main_stem,
                out = synctex_output.path,
            )
    else:
        rename_cmd = ""
        rename_post = ""
        if synctex_output:
            # Even in the no-rename case tectonic writes
            # `<stem>.synctex.gz` next to the PDF; that's exactly where
            # we declared synctex_output to live, so no mv needed.
            pass

    # When a per-document cache snapshot is supplied we extract it into
    # the scratch cache dir before invoking tectonic, so --only-cached
    # finds everything it needs there. Tar extraction adds a few
    # hundred milliseconds at most for typical (~10-100 MB) snapshots.
    if cache_snapshot:
        cache_setup = 'tar -xzf "{}" -C "$TECTONIC_CACHE_DIR"\n'.format(
            cache_snapshot.path,
        )
    else:
        cache_setup = ""

    outputs = [output]
    if synctex_output:
        outputs.append(synctex_output)

    ctx.actions.run_shell(
        command = (
            "set -eu\n" +
            'TECTONIC_CACHE_DIR="$(mktemp -d)"\n' +
            "export TECTONIC_CACHE_DIR\n" +
            'trap "rm -rf \\"$TECTONIC_CACHE_DIR\\"" EXIT\n' +
            cache_setup +
            rename_cmd +
            '"$@"' + rename_post + "\n"
        ),
        arguments = [args],
        inputs = inputs,
        outputs = outputs,
        mnemonic = "TectonicCompile",
        progress_message = "Compiling LaTeX %{label}",
        env = env,
        execution_requirements = {
            # Online mode needs network to fetch packages on first run.
            # With a pinned offline bundle or a per-document cache
            # snapshot the action is fully hermetic and we drop the hint.
            "requires-network": "" if (toolchain.bundle or cache_snapshot) else "1",
        },
    )

    output_groups = {
        "pdf": depset([output]) if outfmt == "pdf" else depset(),
    }
    if synctex_output:
        output_groups["synctex"] = depset([synctex_output])

    return [
        DefaultInfo(files = depset([output])),
        OutputGroupInfo(**output_groups),
        # Propagate the document's transitive sources so live-preview
        # rules (latex_serve) and other meta-tooling can discover them
        # without re-declaring `main`/`srcs`/`deps`.
        LatexInfo(
            srcs = all_srcs,
            search_paths = depset(),
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
        "reproducible": attr.bool(
            doc = "When True, run tectonic in deterministic mode and set " +
                  "SOURCE_DATE_EPOCH=0, producing byte-identical output " +
                  "across runs given identical inputs. Off by default to " +
                  "keep PDF metadata (creation date) reflecting the actual " +
                  "build time. Mutually exclusive with `synctex`.",
            default = False,
        ),
        "synctex": attr.bool(
            doc = "When True, tectonic is invoked with --synctex and the " +
                  "resulting `<name>.synctex.gz` is exposed as an additional " +
                  "output (also surfaced via the `synctex` OutputGroup). " +
                  "Consumed by `latex_serve_web` for click-to-source " +
                  "reverse-sync in the browser. Mutually exclusive with " +
                  "`reproducible` because tectonic's deterministic mode " +
                  "disables SyncTeX output.",
            default = False,
        ),
        "cache": attr.label(
            doc = "Optional cache snapshot tarball (typically produced by " +
                  "`latex_cache_snapshot` and checked into the repository). " +
                  "When set, the action extracts the snapshot into the " +
                  "compile-time `TECTONIC_CACHE_DIR` and runs with " +
                  "`--only-cached`, giving a fully offline, hermetic build " +
                  "without pulling the full ~3 GB tectonic bundle. " +
                  "Takes precedence over the toolchain-level `tectonic.bundle()`.",
            allow_single_file = [".tar.gz", ".tgz"],
        ),
        "tectonic_args": attr.string_list(
            doc = "Extra command-line arguments passed to tectonic. Use " +
                  "sparingly; prefer rule-level attributes when possible.",
        ),
    },
    toolchains = ["//latex/toolchain:toolchain_type"],
)
