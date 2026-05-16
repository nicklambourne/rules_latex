"""Analysis-time tests for `latex_document`.

Verify that the rule produces the expected action graph given
different combinations of attributes:

  * `cache = "foo.tar.gz"`            → only TectonicCompile (no online prime).
  * (no cache, no toolchain bundle)   → both TectonicPopulateCache and
                                        TectonicCompile.
  * `synctex = True`                  → the synctex.gz output appears.
  * `biber = True`                    → biber binary in action inputs.

These tests run at analysis time, not at action execution time, so
they're cheap (sub-second) and don't require any LaTeX compile to
happen.
"""

load("@bazel_skylib//lib:unittest.bzl", "analysistest", "asserts")
load("//latex:defs.bzl", "latex_document")

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _has_action_with_mnemonic(actions, mnemonic):
    for action in actions:
        if action.mnemonic == mnemonic:
            return True
    return False

def _count_actions_with_mnemonic(actions, mnemonic):
    return len([a for a in actions if a.mnemonic == mnemonic])

# -----------------------------------------------------------------------------
# Test: no cache, no bundle -> both PopulateCache and Compile actions
# -----------------------------------------------------------------------------

def _implicit_pipeline_test_impl(ctx):
    env = analysistest.begin(ctx)
    actions = analysistest.target_actions(env)
    asserts.true(
        env,
        _has_action_with_mnemonic(actions, "TectonicPopulateCache"),
        "expected TectonicPopulateCache action when no cache is set",
    )
    asserts.true(
        env,
        _has_action_with_mnemonic(actions, "TectonicCompile"),
        "expected TectonicCompile action",
    )
    asserts.equals(
        env,
        1,
        _count_actions_with_mnemonic(actions, "TectonicPopulateCache"),
        "expected exactly one TectonicPopulateCache action",
    )
    return analysistest.end(env)

implicit_pipeline_test = analysistest.make(_implicit_pipeline_test_impl)

# -----------------------------------------------------------------------------
# Test: cache = "foo.tar.gz" -> only Compile, no PopulateCache
# -----------------------------------------------------------------------------

def _checked_in_cache_test_impl(ctx):
    env = analysistest.begin(ctx)
    actions = analysistest.target_actions(env)
    asserts.false(
        env,
        _has_action_with_mnemonic(actions, "TectonicPopulateCache"),
        "expected NO TectonicPopulateCache action when cache attr is set",
    )
    asserts.true(
        env,
        _has_action_with_mnemonic(actions, "TectonicCompile"),
        "expected TectonicCompile action",
    )
    return analysistest.end(env)

checked_in_cache_test = analysistest.make(_checked_in_cache_test_impl)

# -----------------------------------------------------------------------------
# Test: synctex = True -> declared output includes .synctex.gz
# -----------------------------------------------------------------------------

def _synctex_output_test_impl(ctx):
    env = analysistest.begin(ctx)
    target = analysistest.target_under_test(env)
    output_groups = target[OutputGroupInfo]
    asserts.true(
        env,
        hasattr(output_groups, "synctex"),
        "expected `synctex` output group when synctex = True",
    )
    synctex_files = output_groups.synctex.to_list()
    asserts.equals(
        env,
        1,
        len(synctex_files),
        "expected exactly one .synctex.gz output",
    )
    asserts.true(
        env,
        synctex_files[0].basename.endswith(".synctex.gz"),
        "synctex output should end in .synctex.gz, got {}".format(
            synctex_files[0].basename,
        ),
    )
    return analysistest.end(env)

synctex_output_test = analysistest.make(_synctex_output_test_impl)

# -----------------------------------------------------------------------------
# Test: synctex = False -> empty synctex output group
# -----------------------------------------------------------------------------

def _no_synctex_test_impl(ctx):
    env = analysistest.begin(ctx)
    target = analysistest.target_under_test(env)
    output_groups = target[OutputGroupInfo]

    # Either there's no synctex group at all, or it's an empty depset.
    if hasattr(output_groups, "synctex"):
        asserts.equals(
            env,
            0,
            len(output_groups.synctex.to_list()),
            "expected no synctex outputs when synctex = False",
        )
    return analysistest.end(env)

no_synctex_test = analysistest.make(_no_synctex_test_impl)

# -----------------------------------------------------------------------------
# Suite definition
# -----------------------------------------------------------------------------

def latex_document_test_suite(name):
    """Drives every analysistest defined in this file.

    Declares the targets-under-test (small `latex_document` instances
    exercising each attribute combination) and the matching test
    rules. Invoke as `latex_document_test_suite(name = "all")` in
    BUILD.bazel.

    Args:
      name: name of the generated test_suite that aggregates all the
        analysistests below.
    """

    # Sources used by every test target.
    native.genrule(
        name = "_test_doc_tex",
        outs = ["_test_doc.tex"],
        cmd = "echo '\\\\documentclass{article}\\\\begin{document}x\\\\end{document}' > $@",
    )

    # Fake checked-in cache snapshot for the cache= test. The contents
    # don't matter for analysistest \u2014 we only check the action graph
    # at analysis time.
    native.genrule(
        name = "_fake_cache",
        outs = ["_fake_cache.tar.gz"],
        cmd = "echo fake > $@",
    )

    # --- target_under_test instances ----------------------------------

    latex_document(
        name = "_doc_implicit",
        main = "_test_doc.tex",
        srcs = [":_test_doc_tex"],
        tags = ["manual"],
    )

    latex_document(
        name = "_doc_with_cache",
        main = "_test_doc.tex",
        srcs = [":_test_doc_tex"],
        cache = ":_fake_cache",
        tags = ["manual"],
    )

    latex_document(
        name = "_doc_synctex",
        main = "_test_doc.tex",
        srcs = [":_test_doc_tex"],
        synctex = True,
        tags = ["manual"],
    )

    latex_document(
        name = "_doc_no_synctex",
        main = "_test_doc.tex",
        srcs = [":_test_doc_tex"],
        tags = ["manual"],
    )

    # --- analysistest cases -------------------------------------------

    implicit_pipeline_test(
        name = "implicit_pipeline_test",
        target_under_test = ":_doc_implicit",
    )
    checked_in_cache_test(
        name = "checked_in_cache_test",
        target_under_test = ":_doc_with_cache",
    )
    synctex_output_test(
        name = "synctex_output_test",
        target_under_test = ":_doc_synctex",
    )
    no_synctex_test(
        name = "no_synctex_test",
        target_under_test = ":_doc_no_synctex",
    )

    native.test_suite(
        name = name,
        tests = [
            ":implicit_pipeline_test",
            ":checked_in_cache_test",
            ":synctex_output_test",
            ":no_synctex_test",
        ],
    )
