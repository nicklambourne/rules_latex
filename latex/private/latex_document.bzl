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

### Staging contract (v0.3+)

Both PopulateCache and TectonicCompile stage sources into a work
directory using a **main-rooted layout** before invoking tectonic.
Concretely:

* main.tex lands at `<work>/<main.basename>`.
* Sources under main's package land at the same relative path,
  rooted at the work directory.
* Cross-package sources land under their workspace-relative path
  (e.g. `study/llb/lib/references/refs.bib` from a sibling package).
* The `pkg_files` attribute lets users override placement of any
  specific input, e.g. to stage a cross-package bib file as a sibling
  of main.tex.

Tectonic runs with cwd set to the work directory, so paths in
main.tex like `\\input{sections/foo}` or `\\graphicspath{{./images/}}`
resolve as the author would expect.

### `biber` modes

* `biber = False` (default) — no bibliography processor available.
* `biber = True` — use the rules_latex-vendored biber from the
  toolchain. Fails at analysis time on platforms without an upstream
  biber binary (currently linux/aarch64).
* `biber_strategy = "system"` — escape hatch: propagate $PATH into
  the action so a system-installed biber is found. Less hermetic;
  intended for air-gapped or unsupported-platform builds.
"""

load("@bazel_skylib//rules:common_settings.bzl", "BuildSettingInfo")
load("//latex:providers.bzl", "LatexDocumentInfo", "LatexInfo")

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

def _resolved_pkg_files(ctx):
    """Resolve `pkg_files` to a list of (File, staged-path) pairs.

    Each label key must expand to exactly one file (typically a
    `filegroup` with a single src, or a plain file label).
    """
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

def _populate_cache_action(
        ctx,
        *,
        tectonic,
        biber_file,
        use_system_biber,
        main_in,
        srcs_depset,
        pkg_files,
        output_tarball,
        staging_lib,
        tool):
    """Schedule an online tectonic compile that captures its cache.

    Drives `//tools:tectonic_populate_cache.py`. The output is a
    deterministic, reproducible cache snapshot tarball; subsequent
    TectonicCompile actions consume it offline.
    """
    args = ctx.actions.args()
    args.add("--tectonic", tectonic.path)
    args.add("--main", main_in.path)
    args.add("--output", output_tarball.path)

    for src in srcs_depset.to_list():
        args.add("--src", src.path)
    for (f, rel) in pkg_files:
        args.add("--pkg-file", "{}={}".format(f.path, rel))
    if biber_file:
        args.add("--biber", biber_file.path)

    inputs = depset(
        direct = (
            [main_in, tectonic, tool, staging_lib] +
            ([biber_file] if biber_file else []) +
            [f for (f, _) in pkg_files]
        ),
        transitive = [srcs_depset],
    )

    env = {"LC_ALL": "C.UTF-8"}
    if use_system_biber:
        env["PATH"] = ""

    # Invoke python3 directly so we don't take a rules_python dep.
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
            # The one online step in the implicit pipeline. Bazel's
            # action cache makes this a one-time cost per (sources ×
            # tectonic × bundle) tuple.
            "requires-network": "1",
        },
        use_default_shell_env = use_system_biber,
    )

def _compile_action(
        ctx,
        *,
        tectonic,
        biber_file,
        use_system_biber,
        main_in,
        srcs_depset,
        pkg_files,
        offline_mode,
        offline_source,
        offline_source_path,
        output,
        synctex_output,
        outfmt,
        staging_lib,
        tool):
    """Schedule the TectonicCompile action.

    Drives `//tools:tectonic_compile.py` which stages sources, runs
    tectonic with cwd at the work directory, and copies the resulting
    PDF (and optional .synctex.gz) to Bazel-declared output paths.

    Exactly one of ``offline_source`` (a ``File`` that is part of the
    action's input set) or ``offline_source_path`` (a literal string
    path that is NOT in the input set) must be set. The latter is
    used only by the serve-time cache-override path; see the
    ``//latex:_serve_cache_override`` build setting for the
    hermeticity trade-off.
    """
    if (offline_source == None) == (offline_source_path == None):
        fail(
            "_compile_action: exactly one of offline_source or " +
            "offline_source_path must be set",
        )

    args = ctx.actions.args()
    args.add("--tectonic", tectonic.path)
    args.add("--main", main_in.path)
    args.add("--outfmt", outfmt)
    args.add("--output", output.path)

    if offline_mode == "bundle":
        args.add("--bundle", offline_source.path)
    elif offline_mode == "serve_cache_override":
        # The cache file is not in the Bazel input graph; read it
        # by absolute path at action execution time. We discriminate
        # between a tarball (legacy override path) and a
        # pre-extracted directory (fast path: skips per-action
        # decompression) by the path suffix -- the serve script
        # always names the extracted directory just `cache`, not
        # `cache.tar.gz`.
        if offline_source_path.endswith(".tar.gz") or offline_source_path.endswith(".tgz"):
            args.add("--cache-tarball", offline_source_path)
        else:
            args.add("--cache-dir", offline_source_path)
    else:
        # user_cache and implicit_pipeline both pass a cache tarball.
        args.add("--cache-tarball", offline_source.path)

    if ctx.attr.reproducible:
        args.add("--reproducible")
    if synctex_output:
        args.add("--synctex-output", synctex_output.path)
    for extra in ctx.attr.tectonic_args:
        args.add("--tectonic-arg", extra)

    for src in srcs_depset.to_list():
        args.add("--src", src.path)
    for (f, rel) in pkg_files:
        args.add("--pkg-file", "{}={}".format(f.path, rel))
    if biber_file:
        args.add("--biber", biber_file.path)

    direct_inputs = [main_in, tectonic, tool, staging_lib]
    if offline_source != None:
        direct_inputs.append(offline_source)
    if biber_file:
        direct_inputs.append(biber_file)
    direct_inputs.extend([f for (f, _) in pkg_files])

    inputs = depset(
        direct = direct_inputs,
        transitive = [srcs_depset],
    )

    outputs = [output]
    if synctex_output:
        outputs.append(synctex_output)

    env = {"LC_ALL": "C.UTF-8"}

    ctx.actions.run_shell(
        command = (
            "set -eu\n" +
            ('PATH="${PATH:-/usr/bin:/bin}"\n' if use_system_biber else "") +
            'exec python3 "{tool}" "$@"\n'.format(tool = tool.path)
        ),
        arguments = [args],
        inputs = inputs,
        outputs = outputs,
        mnemonic = "TectonicCompile",
        progress_message = "Compiling LaTeX %{label}",
        env = env,
        execution_requirements = {
            # Fully hermetic in every mode: the cache (user-supplied,
            # bundle, or implicitly populated) is content-addressed
            # and present as an action input.
            "requires-network": "",
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

    synctex_output = None
    if ctx.attr.synctex:
        synctex_output = ctx.actions.declare_file(
            "{}.synctex.gz".format(ctx.label.name),
        )

    toolchain = ctx.toolchains["//latex/toolchain:toolchain_type"].latex_toolchain_info
    tectonic = toolchain.tectonic
    biber_file, use_system_biber = _resolve_biber(ctx, toolchain)
    user_cache = ctx.file.cache
    pkg_files = _resolved_pkg_files(ctx)

    populate_tool = ctx.file._populate_cache_tool
    compile_tool = ctx.file._compile_tool
    staging_lib = ctx.file._staging_lib

    # Read the serve-time cache-override build setting. Non-empty
    # only when `latex_serve_web` is driving the build; see
    # //latex:_serve_cache_override for the design rationale.
    serve_cache_override = (
        ctx.attr._serve_cache_override[BuildSettingInfo].value
    )

    offline_source = None
    offline_source_path = None

    # Decide which offline-mode strategy applies. Precedence:
    #   user cache > toolchain bundle > serve cache override > implicit pipeline.
    #
    # The serve-cache override sits below the explicit user / toolchain
    # paths so it can never silently overrule a user's
    # `cache = "..."` choice (which should remain fully hermetic in
    # all contexts, including under `bazel run //...:serve_web`).
    if user_cache:
        offline_mode = "user_cache"
        offline_source = user_cache
    elif toolchain.bundle:
        offline_mode = "bundle"
        offline_source = toolchain.bundle
    elif serve_cache_override:
        # Serve-time fast path: read the snapshot at an absolute
        # path that's not in the Bazel input graph. The compile
        # action is invalidated by an --action_env nonce passed by
        # latex_serve_web; see the build-setting comment in
        # //latex:BUILD.bazel.
        offline_mode = "serve_cache_override"
        offline_source_path = serve_cache_override
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
            pkg_files = pkg_files,
            output_tarball = offline_source,
            staging_lib = staging_lib,
            tool = populate_tool,
        )

    _compile_action(
        ctx,
        tectonic = tectonic,
        biber_file = biber_file,
        use_system_biber = use_system_biber,
        main_in = main,
        srcs_depset = all_srcs,
        pkg_files = pkg_files,
        offline_mode = offline_mode,
        offline_source = offline_source,
        offline_source_path = offline_source_path,
        output = output,
        synctex_output = synctex_output,
        outfmt = outfmt,
        staging_lib = staging_lib,
        tool = compile_tool,
    )

    output_groups = {
        "pdf": depset([output]) if outfmt == "pdf" else depset(),
    }
    if synctex_output:
        output_groups["synctex"] = depset([synctex_output])

    # Underlying offline strategy, ignoring any serve-time override.
    # This is what gets surfaced on LatexInfo so consumers like
    # `latex_serve_web` can decide whether to interpose their own
    # cache management. The serve-time override is by definition
    # ephemeral and only meaningful inside the running serve loop.
    if user_cache:
        intrinsic_strategy = "user_cache"
    elif toolchain.bundle:
        intrinsic_strategy = "bundle"
    else:
        intrinsic_strategy = "implicit"

    return [
        DefaultInfo(files = depset([output])),
        OutputGroupInfo(**output_groups),
        LatexInfo(
            srcs = all_srcs,
            search_paths = depset(),
            offline_strategy = intrinsic_strategy,
        ),
        LatexDocumentInfo(
            main = main,
            tectonic = tectonic,
            biber = biber_file,
            use_system_biber = use_system_biber,
            pkg_files = pkg_files,
            populate_tool = populate_tool,
            staging_lib = staging_lib,
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
        "pkg_files": attr.label_keyed_string_dict(
            doc = "Override the staged path of specific inputs. Map of " +
                  "label -> relative path under main.tex's work directory. " +
                  "The default main-rooted staging layout puts each src " +
                  "at a sensible place automatically; use `pkg_files` " +
                  "when the default would force you to write a long or " +
                  "`..`-containing path in main.tex. The classic case is " +
                  "a cross-package bib file: declare " +
                  "`pkg_files = {\"//lib/refs:refs.bib\": \"refs.bib\"}` " +
                  "and then write `\\\\addbibresource{refs.bib}` in main.tex.",
            allow_files = True,
        ),
        "tectonic_args": attr.string_list(
            doc = "Extra command-line arguments passed to tectonic. Use " +
                  "sparingly; prefer rule-level attributes when possible.",
        ),
        "_populate_cache_tool": attr.label(
            default = "//tools:tectonic_populate_cache.py",
            allow_single_file = True,
        ),
        "_compile_tool": attr.label(
            default = "//tools:tectonic_compile.py",
            allow_single_file = True,
        ),
        "_staging_lib": attr.label(
            default = "//tools:staging.py",
            allow_single_file = True,
        ),
        "_serve_cache_override": attr.label(
            doc = "Private build-setting dependency. Holds an absolute " +
                  "filesystem path to a pre-primed tectonic cache snapshot " +
                  "when set by `latex_serve_web`. Not intended for direct " +
                  "use; see //latex:_serve_cache_override.",
            default = "//latex:_serve_cache_override",
            providers = [BuildSettingInfo],
        ),
    },
    toolchains = ["//latex/toolchain:toolchain_type"],
)
