"""The `latex_serve_web` rule.

`latex_serve_web` is the in-browser counterpart to `latex_serve`. It
runs a small HTTP server on localhost, watches the document's
transitive sources, rebuilds via `bazel build` on every save, and
pushes a 'reload' event over Server-Sent Events. The connected
browser tab re-renders the PDF via PDF.js, preserving scroll position
so editing doesn't bounce you back to page 1.

Typical usage:

    latex_document(name = "cv", main = "cv.tex", srcs = [...], cache = "cv_cache.tar.gz")

    latex_serve_web(
        name = "cv_web",
        document = ":cv",
    )

Then:

    $ bazel run //:cv_web
    serving live preview at http://127.0.0.1:8765/

Open the URL in your browser; edit the source; watch the PDF update.

Design notes:

* No third-party Python deps. The server uses http.server +
  ThreadingHTTPServer + Server-Sent Events. SSE is enough for a one-way
  "reload" signal and is dramatically simpler than WebSockets to
  implement in stdlib.

* PDF.js is vendored: the pinned pdfjs-dist tarball lives in
  `@rules_latex_pdfjs` (materialised by the `pdfjs` module extension)
  and is served at `/_pdfjs/pdf.mjs` and `/_pdfjs/pdf.worker.mjs` by
  the running server. No CDN dependency at preview time.

* The rebuild path is identical to `latex_serve`: shells out to
  `bazel build`, so live mode and CI use the same toolchain, sandbox,
  and cache. See `DESIGN.md` §4.7.

* Implicit-pipeline serve acceleration. When the served document
  takes the implicit-cache pipeline (no `cache=`, no toolchain
  bundle), the serve script primes a persistent cache snapshot
  under `$BUILD_WORKSPACE_DIRECTORY/.cache/rules_latex/<doc>/` on
  startup and passes it to every `bazel build` via the private
  `//latex:_serve_cache_override` flag. This sidesteps Bazel's
  action-cache invalidation on source-content changes, turning what
  would otherwise be a 30-90 s per-edit online re-prime into a 2-3
  s offline compile. The cache is auto-refreshed on missing-resource
  build failures. See `tools/serve_cache.py` for the cache-manager
  logic, and `//latex:_serve_cache_override` for the rule-side
  wiring.
"""

load("//latex:providers.bzl", "LatexDocumentInfo", "LatexInfo")

def _latex_serve_web_impl(ctx):
    info = ctx.attr.document[LatexInfo]
    srcs = info.srcs.to_list()

    document_label = str(ctx.attr.document.label)

    pdf_relpath = "{}/{}.pdf".format(
        ctx.attr.document.label.package,
        ctx.attr.document.label.name,
    )
    if ctx.attr.document.label.package == "":
        pdf_relpath = "{}.pdf".format(ctx.attr.document.label.name)

    # If the document was built with synctex = True, latex_document
    # exposes the .synctex.gz file via an OutputGroup. Pluck it out so
    # the server can offer reverse-sync; absent that output group,
    # SyncTeX features are silently disabled in the browser.
    synctex_files = []
    if OutputGroupInfo in ctx.attr.document:
        groups = ctx.attr.document[OutputGroupInfo]
        if hasattr(groups, "synctex"):
            synctex_files = groups.synctex.to_list()
    synctex_relpath = ""
    if synctex_files:
        sf = synctex_files[0]
        synctex_relpath = "{}/{}".format(
            sf.owner.package,
            sf.basename,
        ) if sf.owner.package else sf.basename

    watched_paths = []
    for src in srcs:
        if src.owner.workspace_name:
            continue
        watched_paths.append(src.short_path)

    pdfjs_lib = ctx.file._pdfjs_lib
    pdfjs_worker = ctx.file._pdfjs_worker
    pdf_chunks_lib = ctx.file._pdf_chunks_lib

    # Decide whether to plumb in the serve-time cache override.
    # Only the implicit-pipeline path needs it; documents with
    # `cache=` or a toolchain bundle already have hermetic, fast
    # rebuilds.
    offline_strategy = info.offline_strategy
    enable_serve_cache = (offline_strategy == "implicit")

    # Runfiles for the cache manager. Only materialised when the
    # implicit-pipeline path applies, otherwise we emit empty
    # placeholders so the generated script can compile without
    # holding onto inputs it'll never read.
    serve_cache_runfiles = []
    prime_srcs_lines = []
    prime_pkg_files_lines = []
    prime_main_path = ""
    prime_tectonic_path = ""
    prime_populate_tool_path = ""
    prime_serve_cache_path = ""
    prime_staging_lib_path = ""
    prime_biber_path = ""
    prime_use_system_biber = ""

    if enable_serve_cache:
        if LatexDocumentInfo not in ctx.attr.document:
            fail(
                ("latex_serve_web target {} attached to document {} " +
                 "which doesn't provide LatexDocumentInfo. This is a " +
                 "rules_latex bug: latex_document should always provide " +
                 "this when it provides LatexInfo with offline_strategy " +
                 "= \"implicit\".").format(ctx.label, ctx.attr.document.label),
            )
        doc_info = ctx.attr.document[LatexDocumentInfo]
        serve_cache_lib = ctx.file._serve_cache_lib
        serve_cache_runfiles = [
            serve_cache_lib,
            doc_info.populate_tool,
            doc_info.staging_lib,
            doc_info.tectonic,
            doc_info.main,
        ]
        # The source-side runfiles we will hand to the populate tool
        # via --src on serve startup. Stored as short_paths so the
        # serve_web.py.tpl substitution can resolve them against the
        # workspace at runtime.
        for src in srcs:
            if src.owner.workspace_name:
                # Cross-repo sources don't have a workspace-relative
                # path that the populate tool can stage. The implicit
                # pipeline already handles them via the same tool
                # path (the action runs at execroot, where these paths
                # do resolve), but the serve-time prime runs from the
                # workspace root, so cross-repo sources would fail
                # staging. We bail to a warning + skip; documents
                # using cross-repo sources can either use cache=
                # explicitly or live with the Bazel-internal implicit
                # pipeline (30-90 s on edits).
                # buildifier: disable=print
                print(
                    ("latex_serve_web({}): document {} includes a " +
                     "cross-repo source {} which can't be staged from " +
                     "the workspace root at serve time; the serve-cache " +
                     "fast-path won't engage for this target.").format(
                        ctx.label, ctx.attr.document.label, src.short_path,
                    ),
                )
                enable_serve_cache = False
                serve_cache_runfiles = []
                break
            prime_srcs_lines.append(src.short_path)
            serve_cache_runfiles.append(src)
        for (pf_file, pf_rel) in doc_info.pkg_files:
            prime_pkg_files_lines.append(
                "{}={}".format(pf_file.short_path, pf_rel),
            )
            serve_cache_runfiles.append(pf_file)
        if enable_serve_cache:
            prime_main_path = doc_info.main.short_path
            prime_tectonic_path = doc_info.tectonic.short_path
            prime_populate_tool_path = doc_info.populate_tool.short_path
            prime_serve_cache_path = serve_cache_lib.short_path
            prime_staging_lib_path = doc_info.staging_lib.short_path
            if doc_info.biber:
                prime_biber_path = doc_info.biber.short_path
                serve_cache_runfiles.append(doc_info.biber)
            prime_use_system_biber = "1" if doc_info.use_system_biber else ""

    server_script = ctx.actions.declare_file(ctx.label.name + ".py")
    ctx.actions.expand_template(
        template = ctx.file._server_template,
        output = server_script,
        substitutions = {
            "{{DOCUMENT_LABEL}}": document_label,
            "{{PDF_RELPATH}}": pdf_relpath,
            "{{SYNCTEX_RELPATH}}": synctex_relpath,
            "{{WATCHED_PATHS}}": "\n".join(watched_paths),
            "{{POLL_INTERVAL}}": str(ctx.attr.poll_interval_ms),
            "{{DEBOUNCE_MS}}": str(ctx.attr.debounce_ms),
            "{{DEBOUNCE_MAX_MS}}": str(ctx.attr.debounce_max_ms),
            "{{PORT}}": str(ctx.attr.port),
            "{{DOCUMENT_NAME}}": ctx.attr.document.label.name,
            "{{PDFJS_LIB_RUNFILE}}": pdfjs_lib.short_path,
            "{{PDFJS_WORKER_RUNFILE}}": pdfjs_worker.short_path,
            "{{OPEN_ON_START}}": "1" if ctx.attr.open_on_start else "0",
            "{{PDF_CHUNKS_RUNFILE}}": pdf_chunks_lib.short_path,
            "{{ENABLE_SERVE_CACHE}}": "1" if enable_serve_cache else "",
            "{{SERVE_CACHE_RUNFILE}}": prime_serve_cache_path,
            "{{PRIME_MAIN_RUNFILE}}": prime_main_path,
            "{{PRIME_TECTONIC_RUNFILE}}": prime_tectonic_path,
            "{{PRIME_POPULATE_TOOL_RUNFILE}}": prime_populate_tool_path,
            "{{PRIME_STAGING_LIB_RUNFILE}}": prime_staging_lib_path,
            "{{PRIME_BIBER_RUNFILE}}": prime_biber_path,
            "{{PRIME_USE_SYSTEM_BIBER}}": prime_use_system_biber,
            "{{PRIME_SRCS}}": "\n".join(prime_srcs_lines),
            "{{PRIME_PKG_FILES}}": "\n".join(prime_pkg_files_lines),
        },
    )

    launcher = ctx.actions.declare_file(ctx.label.name + ".sh")
    launcher_content = """\
#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${{BUILD_WORKSPACE_DIRECTORY:-}}" ]]; then
    echo "ERROR: this target must be invoked with 'bazel run', not 'bazel build'." >&2
    exit 1
fi

RUNFILES="$(pwd)"
PYTHON="${{PYTHON:-python3}}"
exec "$PYTHON" "$RUNFILES/{server}" "$BUILD_WORKSPACE_DIRECTORY" "$RUNFILES" "$@"
""".format(server = server_script.short_path)
    ctx.actions.write(launcher, launcher_content, is_executable = True)

    runfiles = ctx.runfiles(
        files = (
            [server_script, pdfjs_lib, pdfjs_worker, pdf_chunks_lib] +
            serve_cache_runfiles
        ),
    )
    return [DefaultInfo(executable = launcher, runfiles = runfiles)]

latex_serve_web = rule(
    implementation = _latex_serve_web_impl,
    doc = "Browser-based live-preview server for a latex_document.",
    executable = True,
    attrs = {
        "document": attr.label(
            doc = "The latex_document (or any rule providing LatexInfo) " +
                  "to watch and rebuild.",
            providers = [[LatexInfo]],
            mandatory = True,
        ),
        "port": attr.int(
            doc = "TCP port to bind the preview server to (localhost-only).",
            default = 8765,
        ),
        "poll_interval_ms": attr.int(
            doc = "How often the watcher checks for source-file changes, " +
                  "in milliseconds. The watcher is a polling loop (no " +
                  "third-party `watchdog`/inotify dependency), so this " +
                  "is the amortised cost of one stat() per watched file " +
                  "per interval. 80 ms keeps perceived save-to-preview " +
                  "latency under 100 ms while staying cheap. Independent " +
                  "of `debounce_ms`: the poll interval is how fast we " +
                  "*notice* a change; the debounce window is how long we " +
                  "*wait* after a change before triggering a build.",
            default = 80,
        ),
        "open_on_start": attr.bool(
            doc = "If True, open the preview automatically once the server " +
                  "starts. When the launching terminal belongs to a " +
                  "VS Code-family editor (VS Code, Cursor, VSCodium — " +
                  "detected via TERM_PROGRAM), the preview is opened as a " +
                  "Simple Browser tab in that editor via its CLI " +
                  "(`code --open-url`, `cursor --open-url`, " +
                  "`codium --open-url`). Otherwise it falls back to the " +
                  "system default web browser. JetBrains IDEs and other " +
                  "terminals without a Simple Browser equivalent fall back " +
                  "to the web-browser path. The plain http URL is always " +
                  "printed regardless, so users can copy/paste manually.",
            default = False,
        ),
        "debounce_ms": attr.int(
            doc = "How many milliseconds of source-idle to require " +
                  "after a detected change before triggering a rebuild. " +
                  "Coalesces bursts of writes (e.g. format-on-save then " +
                  "user-save, or editors that write multiple files near-" +
                  "simultaneously) into a single build. Set to 0 to " +
                  "disable debouncing (rebuild on every poll-detected " +
                  "change; reproduces pre-v0.3.3 behaviour). The default " +
                  "of 250 ms is invisible to the user because the build " +
                  "itself takes longer than the debounce window.",
            default = 250,
        ),
        "debounce_max_ms": attr.int(
            doc = "Safety net for the debouncer: never wait more than " +
                  "this many milliseconds before firing a build, even " +
                  "if changes keep arriving. Without this cap, a user " +
                  "typing continuously into an editor with " +
                  "fast-autosave-on-every-keystroke would never see a " +
                  "rebuild. 1500 ms matches the upper bound where a " +
                  "user typically expects 'okay, something should " +
                  "happen now'.",
            default = 1500,
        ),
        "_server_template": attr.label(
            default = "//latex/private:serve_web.py.tpl",
            allow_single_file = True,
        ),
        "_pdfjs_lib": attr.label(
            default = "@rules_latex_pdfjs//:pdf.mjs",
            allow_single_file = True,
        ),
        "_pdfjs_worker": attr.label(
            default = "@rules_latex_pdfjs//:pdf.worker.mjs",
            allow_single_file = True,
        ),
        "_serve_cache_lib": attr.label(
            default = "//tools:serve_cache.py",
            allow_single_file = True,
        ),
        "_pdf_chunks_lib": attr.label(
            doc = "Pure-Python PDF chunker used to compute the " +
                  "content-addressed manifest after each build. " +
                  "Loaded dynamically from runfiles by the serve " +
                  "script; see tools/pdf_chunks.py.",
            default = "//tools:pdf_chunks.py",
            allow_single_file = True,
        ),
    },
)
