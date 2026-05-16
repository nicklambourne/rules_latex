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

* PDF.js is loaded from cdn.jsdelivr.net at page-load time. We
  deliberately don't vendor a ~3 MB JS bundle into the rule set. Users
  on air-gapped machines can fall back to `latex_serve` (system
  viewer) or self-host a PDF.js mirror via the `pdfjs_version` attr.

* The rebuild path is identical to `latex_serve`: shells out to
  `bazel build`, so live mode and CI use the same toolchain, sandbox,
  and cache. See `DESIGN.md` §4.7.
"""

load("//latex:providers.bzl", "LatexInfo")

# Default PDF.js version. Pinned at the rule level so users get
# deterministic behaviour; override via the `pdfjs_version` attr if you
# need a different release.
_DEFAULT_PDFJS_VERSION = "5.4.149"

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

    watched_paths = []
    for src in srcs:
        if src.owner.workspace_name:
            continue
        watched_paths.append(src.short_path)

    server_script = ctx.actions.declare_file(ctx.label.name + ".py")
    ctx.actions.expand_template(
        template = ctx.file._server_template,
        output = server_script,
        substitutions = {
            "{{DOCUMENT_LABEL}}": document_label,
            "{{PDF_RELPATH}}": pdf_relpath,
            "{{WATCHED_PATHS}}": "\n".join(watched_paths),
            "{{POLL_INTERVAL}}": str(ctx.attr.poll_interval_ms),
            "{{PORT}}": str(ctx.attr.port),
            "{{DOCUMENT_NAME}}": ctx.attr.document.label.name,
            "{{PDFJS_VERSION}}": ctx.attr.pdfjs_version,
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
exec "$PYTHON" "$RUNFILES/{server}" "$BUILD_WORKSPACE_DIRECTORY" "$@"
""".format(server = server_script.short_path)
    ctx.actions.write(launcher, launcher_content, is_executable = True)

    runfiles = ctx.runfiles(files = [server_script])
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
                  "in milliseconds.",
            default = 250,
        ),
        "pdfjs_version": attr.string(
            doc = "Pinned PDF.js version pulled from cdn.jsdelivr.net at " +
                  "page-load time. Override to point at a different upstream " +
                  "release (or a self-hosted mirror that mimics the " +
                  "pdfjs-dist npm package layout).",
            default = _DEFAULT_PDFJS_VERSION,
        ),
        "_server_template": attr.label(
            default = "//latex/private:serve_web.py.tpl",
            allow_single_file = True,
        ),
    },
)
