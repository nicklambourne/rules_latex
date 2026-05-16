"""The `latex_serve` rule.

`latex_serve` produces a `bazel run`-able developer command that watches a
LaTeX document's transitive sources and rebuilds the PDF on every save.
It's the rules_latex equivalent of `cargo watch` or an Overleaf-style
live preview: keep your editor open in one window and the PDF viewer in
another, and edits propagate within a second or two.

Typical usage:

    latex_document(name = "cv", main = "cv.tex", srcs = [...], deps = [...])

    latex_serve(
        name = "cv_serve",
        document = ":cv",
    )

Then:

    $ bazel run //:cv_serve
    Built bazel-bin/cv.pdf in 3.2s. Opened in Preview.
    [edit and save cv.tex]
    Built bazel-bin/cv.pdf in 0.6s.

The rebuild step is just `bazel build //:cv` under the hood, so it uses
the same toolchain, sandbox, and cache as a regular build. In
particular, if `:cv` is configured with a cache snapshot via the
`cache = ...` attribute, live rebuilds run fully offline and typically
finish in well under a second after the first warm-up build.

The PDF viewer is launched once on the first successful build via the
platform's default open command (`open` on macOS, `xdg-open` on Linux,
`start` on Windows). Whether subsequent rebuilds appear automatically
in the viewer depends on the viewer itself; macOS Preview, Linux
Evince, and Okular all watch the file and refresh on change.
"""

load("//latex:providers.bzl", "LatexInfo")

def _latex_serve_impl(ctx):
    info = ctx.attr.document[LatexInfo]
    srcs = info.srcs.to_list()

    # The document's build target. `str(label)` produces the canonical
    # `//pkg:name` form for in-workspace labels and `@repo//pkg:name`
    # for external ones, which is exactly what we want to pass to
    # `bazel build` from inside the serve script.
    document_label = str(ctx.attr.document.label)

    # The output PDF path under bazel-bin. We assume the document's
    # default output is `<name>.pdf`, matching latex_document's behaviour.
    pdf_relpath = "{}/{}.pdf".format(
        ctx.attr.document.label.package,
        ctx.attr.document.label.name,
    )
    if ctx.attr.document.label.package == "":
        pdf_relpath = "{}.pdf".format(ctx.attr.document.label.name)

    # Build a newline-separated list of workspace-relative source files
    # that the Python watcher should monitor. Sources from external
    # repos are skipped — we only watch first-party files the user is
    # likely to edit. (Edits to external repos would require a bazel
    # sync anyway, so the user must intentionally rerun the serve
    # target after those.)
    watched_paths = []
    for src in srcs:
        if src.owner.workspace_name:
            continue
        watched_paths.append(src.short_path)

    watcher_script = ctx.actions.declare_file(ctx.label.name + ".py")
    ctx.actions.expand_template(
        template = ctx.file._watcher_template,
        output = watcher_script,
        substitutions = {
            "{{DOCUMENT_LABEL}}": document_label,
            "{{PDF_RELPATH}}": pdf_relpath,
            "{{WATCHED_PATHS}}": "\n".join(watched_paths),
            "{{POLL_INTERVAL}}": str(ctx.attr.poll_interval_ms),
            "{{OPEN_PDF}}": "1" if ctx.attr.open_pdf else "0",
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
exec "$PYTHON" "$RUNFILES/{watcher}" "$BUILD_WORKSPACE_DIRECTORY" "$@"
""".format(watcher = watcher_script.short_path)
    ctx.actions.write(launcher, launcher_content, is_executable = True)

    runfiles = ctx.runfiles(files = [watcher_script])
    return [DefaultInfo(executable = launcher, runfiles = runfiles)]

latex_serve = rule(
    implementation = _latex_serve_impl,
    doc = "Watch a latex_document's sources and rebuild on every save.",
    executable = True,
    attrs = {
        "document": attr.label(
            doc = "The latex_document (or any rule providing LatexInfo) " +
                  "to watch and rebuild.",
            providers = [[LatexInfo]],
            mandatory = True,
        ),
        "poll_interval_ms": attr.int(
            doc = "How often the watcher checks for source-file changes, " +
                  "in milliseconds. Polling-based to avoid third-party " +
                  "dependencies; bumping this trades latency for CPU.",
            default = 250,
        ),
        "open_pdf": attr.bool(
            doc = "If True, open the built PDF in the system's default " +
                  "viewer after the first successful build.",
            default = True,
        ),
        "_watcher_template": attr.label(
            default = "//latex/private:serve_watcher.py.tpl",
            allow_single_file = True,
        ),
    },
)
