"""The `pdfjs` module extension.

Materialises the `@rules_latex_pdfjs` repository that holds the pinned
PDF.js distribution used by `latex_serve_web`. Consumers' MODULE.bazel
does:

    pdfjs = use_extension("@rules_latex//latex/web:extensions.bzl", "pdfjs")
    use_repo(pdfjs, "rules_latex_pdfjs")

There's nothing user-tunable on the extension yet — the version is
pinned in `//latex/private:pdfjs_versions.bzl`. A future
`pdfjs.version(name = ...)` tag could let consumers bump it
independently of a rules_latex release if they need a newer PDF.js
release before we cut one.
"""

load("//latex/web:repositories.bzl", "pdfjs_repository")

_REPO_NAME = "rules_latex_pdfjs"

def _pdfjs_impl(_module_ctx):
    pdfjs_repository(name = _REPO_NAME)

pdfjs = module_extension(
    implementation = _pdfjs_impl,
    doc = "Provides @rules_latex_pdfjs, the bundled PDF.js distribution.",
)
