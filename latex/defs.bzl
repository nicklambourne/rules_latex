"""Public API for rules_latex.

Load symbols from here:

    load("@rules_latex//latex:defs.bzl", "latex_document", "latex_library", "latex_pkg", "latex_test")
"""

load(
    "//latex:providers.bzl",
    _LatexInfo = "LatexInfo",
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
    "//latex/private:latex_test.bzl",
    _latex_test = "latex_test",
)

latex_document = _latex_document
latex_library = _latex_library
latex_pkg = _latex_pkg
latex_test = _latex_test
LatexInfo = _LatexInfo
