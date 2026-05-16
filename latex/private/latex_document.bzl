"""The `latex_document` rule.

Compiles a LaTeX source tree into a PDF (or other tectonic-supported format)
using the resolved tectonic toolchain. The rule transitively collects sources
from any `deps` that provide LatexInfo (i.e. `latex_library` / `latex_pkg`).

Three offline-mode strategies, in priority order:

1. `cache = "<file>.tar.gz"` — extract a user-supplied cache snapshot
   into the tectonic cache before compile, run --only-cached. Fully
   offline, fully reproducible, snapshot is checked-in.
2. Toolchain-level `tectonic.bundle()` — pass --bundle <pinned-3GB-tar>
   --only-cached. Fully offline, snapshot is fetched once via
   download_and_extract.
3. **Implicit pipeline** (new in v0.2): when neither (1) nor (2) is set,
   the rule synthesises a PopulateCache action that runs tectonic ONCE
   in online mode and captures the resulting cache directory as a
   tar.gz. That tarball is then an input to TectonicCompile, which
   runs with --only-cached. Bazel's action cache means the
   PopulateCache result is reused across builds and shared via the
   remote cache; only adding a new \\usepackage triggers a re-run.

`biber` modes:

* `biber = False` (default) — no bibliography processor available.
* `biber = True` — use the rules_latex-vendored biber from the
  toolchain. Fails at analysis time on platforms without an upstream
  biber binary (currently linux/aarch64).
* `biber = "system"` — escape hatch: propagate $PATH into the action
  so a system-installed biber is found. Less hermetic; intended for
  air-gapped or unsupported-platform builds.
"""

load("//latex:providers.bzl", "LatexInfo")

_OUTFMTS = ["pdf", "html", "xdv", "aux"]

_BIBER_STRATEGIES = ["toolchain", "system"]

def _collect_transitive_srcs(deps):
    return [dep[LatexInfo].srcs for dep in deps if LatexInfo in dep]

def _resolve_biber(ctx, toolchain):
    """Return (biber_file_or_None, use_system) given the rule's attrs.

    Validates platform support: `biber = True` on a platform whose
    toolchain has no biber attribute fails loudly here rather than
    blowing up at compile time.
    """
    if not ctx.attr.biber:
        return (None, False)
    if ctx.attr.biber_strategy == "system":
        return (None, True)

    # Default strategy: use the rules_latex-vendored binary.
    if toolchain.biber == None:
        fail(
            "latex_document(biber = True) on {}, but the resolved " +
            "toolchain does not provide a biber binary. The most " +
            "common cause is running on linux/aarch64, which has no " +
            "upstream biber prebuilt. Workarounds: " +
            "(a) cross-compile on x86_64; " +
            "(b) install biber via your distro and set " +
            "`biber_strategy = \"system\"` on this target; " +
            "(c) wait for v0.3 which builds biber from source. " +
            "See DESIGN.md §4.9.".format(ctx.label),
        )
    return (toolchain.biber, False)

def _shell_setup_block(*, cache_setup, biber_setup, rename_setup):
    """Stitch the action's prologue commands together.

    All three are bash snippets (possibly empty) that are concatenated
    inside `ctx.actions.run_shell`. Centralising them here keeps the
    main impl readable.
    """
    return "".join([
        "set -eu\n",
        'TECTONIC_CACHE_DIR="$(mktemp -d)"\n',
        "export TECTONIC_CACHE_DIR\n",
        'trap "rm -rf \\"$TECTONIC_CACHE_DIR\\"" EXIT\n',
        biber_setup,
        cache_setup,
        rename_setup,
    ])

def _biber_setup_command(biber_file):
    """Stage the biber binary onto PATH for the action.

    Bazel's sandbox passes a scrubbed PATH; tectonic shells out to
    `biber` by basename, so we need it findable. We symlink the
    binary into a per-action scratch dir and prepend that to PATH.
    """
    return (
        '_BIBER_DIR="$(mktemp -d)"\n' +
        'ln -s "$PWD/{}" "$_BIBER_DIR/biber"\n'.format(biber_file.path) +
        'export PATH="$_BIBER_DIR:${PATH:-/usr/bin:/bin}"\n'
    )

def _populate_cache_action(ctx, *, tectonic, biber_file, use_system_biber, main_in, srcs_depset, output_tarball):
    """Schedule an online tectonic compile that captures its cache.

    Drives `//tools:make_cache_snapshot.py`. The output is a
    deterministic, reproducible cache snapshot tarball; subsequent
    TectonicCompile actions consume it offline.
    """
    tool = ctx.file._cache_snapshot_tool

    args = ctx.actions.args()
    args.add("--tectonic", tectonic.path)
    args.add("--main", main_in.path)
    args.add("--output", output_tarball.path)
    # We deliberately don't pass --src-root: the tool computes the
    # deepest common ancestor of (main + all srcs), which is the
    # right thing whether sources live in a single package or span
    # multiple (cross-package latex_pkg deps, shared image dirs etc).

    # Each transitive source is passed via --src so the snapshot tool
    # can stage them all into a working dir before invoking tectonic.
    for src in srcs_depset.to_list():
        args.add("--src", src.path)
    if biber_file:
        args.add("--biber", biber_file.path)

    inputs = depset(
        direct = [main_in, tectonic, tool] +
                 ([biber_file] if biber_file else []),
        transitive = [srcs_depset],
    )

    # Environment for the action: the snapshot tool itself only needs
    # a UTF-8 locale; biber path-prepending is done by the tool when
    # --biber is set. If the user opted for system biber, we need
    # PATH to propagate so the tool's subprocess can find it.
    env = {"LC_ALL": "C.UTF-8"}
    if use_system_biber:
        env["PATH"] = ""  # populated by execution_requirements below

    # We invoke python3 directly so we don't have to ship the tool as
    # a py_binary (which would drag in rules_python).
    ctx.actions.run_shell(
        command = (
            "set -eu\n" +
            ('PATH="${PATH:-/usr/bin:/bin}"\n' if use_system_biber else "") +
            'exec python3 "{tool}" "$@"\n'.format(tool = tool.path)
        ),
        arguments = [args],
        inputs = inputs,
        outputs = [output_tarball],
        mnemonic = "TectonicPopulateCache",
        progress_message = "Populating tectonic cache for %{label}",
        env = env,
        execution_requirements = {
            # PopulateCache is the one online step. The action cache
            # makes this a one-time cost per (sources × tectonic
            # version × bundle version) tuple — subsequent builds
            # reuse the cached output.
            "requires-network": "1",
        },
        use_default_shell_env = use_system_biber,
    )

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

    synctex_output = None
    if ctx.attr.synctex:
        synctex_output = ctx.actions.declare_file(
            "{}.synctex.gz".format(ctx.label.name),
        )

    toolchain = ctx.toolchains["//latex/toolchain:toolchain_type"].latex_toolchain_info
    tectonic = toolchain.tectonic
    biber_file, use_system_biber = _resolve_biber(ctx, toolchain)
    user_cache = ctx.file.cache

    # Decide which offline-mode strategy applies. Precedence: user
    # cache > toolchain bundle > implicit pipeline.
    if user_cache:
        offline_mode = "user_cache"
        offline_source = user_cache
    elif toolchain.bundle:
        offline_mode = "bundle"
        offline_source = toolchain.bundle
    else:
        offline_mode = "implicit_pipeline"
        offline_source = ctx.actions.declare_file(
            "_{}_implicit_cache.tar.gz".format(ctx.label.name),
        )
        _populate_cache_action(
            ctx,
            tectonic = tectonic,
            biber_file = biber_file,
            use_system_biber = use_system_biber,
            main_in = main,
            srcs_depset = all_srcs,
            output_tarball = offline_source,
        )

    # --- TectonicCompile action --------------------------------------

    args = ctx.actions.args()
    args.add(tectonic.path)
    args.add("-X")
    args.add("compile")
    args.add("--outfmt", outfmt)
    args.add("--outdir", work_subdir)
    args.add("--keep-logs")
    if offline_mode == "bundle":
        args.add("--bundle", offline_source.path)
        args.add("--only-cached")
    else:
        # user_cache and implicit_pipeline both pre-populate
        # TECTONIC_CACHE_DIR from a tar.gz, then run with --only-cached.
        args.add("--only-cached")
    if ctx.attr.reproducible:
        args.add("-Z")
        args.add("deterministic-mode")
    if ctx.attr.synctex:
        args.add("--synctex")
    for extra in ctx.attr.tectonic_args:
        args.add(extra)
    args.add(main.path)

    inputs = depset(
        direct = [main, tectonic, offline_source] +
                 ([biber_file] if biber_file else []),
        transitive = [all_srcs],
    )

    env = {"LC_ALL": "C.UTF-8"}
    if ctx.attr.reproducible:
        env["SOURCE_DATE_EPOCH"] = "0"

    # Cache-setup snippet: for the bundle path the cache is unused, for
    # both tarball paths we extract it into the scratch cache dir.
    if offline_mode in ("user_cache", "implicit_pipeline"):
        cache_setup = 'tar -xzf "{}" -C "$TECTONIC_CACHE_DIR"\n'.format(
            offline_source.path,
        )
    else:
        cache_setup = ""

    biber_setup = _biber_setup_command(biber_file) if biber_file else ""

    # Rename-setup snippet: tectonic always names outputs after the
    # input stem; when needs_rename we direct output to a private
    # subdir and mv after.
    if needs_rename:
        rename_setup = 'mkdir -p "{work}"\n'.format(work = work_subdir)
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
        rename_setup = ""
        rename_post = ""

    outputs = [output]
    if synctex_output:
        outputs.append(synctex_output)

    ctx.actions.run_shell(
        command = (
            _shell_setup_block(
                cache_setup = cache_setup,
                biber_setup = biber_setup,
                rename_setup = rename_setup,
            ) +
            '"$@"' + rename_post + "\n"
        ),
        arguments = [args],
        inputs = inputs,
        outputs = outputs,
        mnemonic = "TectonicCompile",
        progress_message = "Compiling LaTeX %{label}",
        env = env,
        execution_requirements = {
            # TectonicCompile is fully hermetic in every mode: the
            # cache (user-supplied, bundle, or implicitly populated)
            # is content-addressed and present as an action input.
            "requires-network": "",
        },
        use_default_shell_env = use_system_biber,
    )

    output_groups = {
        "pdf": depset([output]) if outfmt == "pdf" else depset(),
    }
    if synctex_output:
        output_groups["synctex"] = depset([synctex_output])

    return [
        DefaultInfo(files = depset([output])),
        OutputGroupInfo(**output_groups),
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
                  "without pulling the full ~3 GB tectonic bundle or " +
                  "running an online prime. Takes precedence over the " +
                  "toolchain-level `tectonic.bundle()` and over the " +
                  "implicit cache pipeline.",
            allow_single_file = [".tar.gz", ".tgz"],
        ),
        "biber": attr.bool(
            doc = "Enable biber bibliography processing. When True, " +
                  "tectonic can shell out to a `biber` binary at compile " +
                  "time, resolving `\\\\addbibresource`/`\\\\bibliography` " +
                  "directives via biblatex. The binary comes from the " +
                  "rules_latex toolchain by default; set " +
                  "`biber_strategy = \"system\"` to use a system install " +
                  "instead.",
            default = False,
        ),
        "biber_strategy": attr.string(
            doc = "Which biber binary to use when `biber = True`. " +
                  "`\"toolchain\"` (default) uses the rules_latex-vendored " +
                  "biber 2.17; fails at analysis time on platforms without " +
                  "an upstream prebuilt (currently linux/aarch64). " +
                  "`\"system\"` propagates $PATH so a system-installed " +
                  "biber is found; less hermetic, intended as an escape " +
                  "hatch.",
            default = "toolchain",
            values = _BIBER_STRATEGIES,
        ),
        "tectonic_args": attr.string_list(
            doc = "Extra command-line arguments passed to tectonic. Use " +
                  "sparingly; prefer rule-level attributes when possible.",
        ),
        "_cache_snapshot_tool": attr.label(
            default = "//tools:make_cache_snapshot.py",
            allow_single_file = True,
        ),
    },
    toolchains = ["//latex/toolchain:toolchain_type"],
)
