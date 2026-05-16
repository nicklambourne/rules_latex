"""Public API for rules_latex.

Load symbols from here:

    load("@rules_latex//latex:defs.bzl",
         "latex_document", "latex_library", "latex_pkg",
         "latex_test", "latex_cache_snapshot",
         "latex_serve", "latex_serve_web")
"""

load(
    "//latex:providers.bzl",
    _LatexInfo = "LatexInfo",
)
load(
    "//latex/private:latex_cache_snapshot.bzl",
    _latex_cache_snapshot = "latex_cache_snapshot",
)
load(
    "//latex/private:latex_document.bzl",
    _latex_document = "latex_document",
)
load(
    "//latex/private:latex_library.bzl",
    _latex_library = "latex_library",
)
load(
    "//latex/private:latex_pkg.bzl",
    _latex_pkg = "latex_pkg",
)
load(
    "//latex/private:latex_serve.bzl",
    _latex_serve = "latex_serve",
)
load(
    "//latex/private:latex_serve_web.bzl",
    _latex_serve_web = "latex_serve_web",
)
load(
    "//latex/private:latex_test.bzl",
    _latex_test = "latex_test",
)

latex_cache_snapshot = _latex_cache_snapshot
latex_document = _latex_document
latex_library = _latex_library
latex_pkg = _latex_pkg
latex_serve = _latex_serve
latex_serve_web = _latex_serve_web
latex_test = _latex_test
LatexInfo = _LatexInfo
