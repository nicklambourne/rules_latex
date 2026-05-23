"""Analysis-time tests for `latex_document`.

Verify that the rule produces the expected action graph given
different combinations of attributes:

  * `cache = "foo.tar.gz"`            → only TectonicCompile (no online prime).
  * (no cache, no toolchain bundle)   → both TectonicPopulateCache and
                                        TectonicCompile.
  * `synctex = True`                  → the synctex.gz output appears.
  * `biber = True`                    → biber binary in action inputs.
  * action-schema canary               → declared-output set + env wiring.

These tests run at analysis time, not at action execution time, so
they're cheap (sub-second) and don't require any LaTeX compile to
happen.
"""

load("@bazel_skylib//lib:unittest.bzl", "analysistest", "asserts")
load("//latex:defs.bzl", "latex_document")

# Mirror of `_EXPECTED_ACTION_SCHEMA` from
# `//latex/private:action_schema.bzl`. Inlined rather than loaded
# because the buildifier `bzl-visibility` rule discourages loading
# from `//latex/private/*` outside `//latex/*`. When you bump the
# constant in action_schema.bzl, update this snapshot too — that's
# what makes the canary test actually catch forgotten bumps.
_EXPECTED_ACTION_SCHEMA = "v1"

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
# Test: pkg_files -> the override file appears in TectonicCompile's inputs
#                    and is passed via --pkg-file
# -----------------------------------------------------------------------------

def _pkg_files_test_impl(ctx):
    env = analysistest.begin(ctx)
    actions = analysistest.target_actions(env)
    compile_actions = [a for a in actions if a.mnemonic == "TectonicCompile"]
    asserts.equals(env, 1, len(compile_actions), "expected exactly one TectonicCompile action")
    compile_action = compile_actions[0]

    # The pkg_files file should be wired into the action's inputs.
    input_paths = [f.path for f in compile_action.inputs.to_list()]
    found_bib = False
    for p in input_paths:
        if p.endswith("_pkg_files_bib.bib"):
            found_bib = True
            break
    asserts.true(
        env,
        found_bib,
        "expected the pkg_files override file in TectonicCompile inputs, got: {}".format(input_paths),
    )

    # And the action's argv should include a --pkg-file flag.
    argv = compile_action.argv or []
    has_pkg_file_flag = False
    for arg in argv:
        if arg == "--pkg-file":
            has_pkg_file_flag = True
            break
    asserts.true(
        env,
        has_pkg_file_flag,
        "expected --pkg-file in TectonicCompile argv, got: {}".format(argv),
    )
    return analysistest.end(env)

pkg_files_test = analysistest.make(_pkg_files_test_impl)

# -----------------------------------------------------------------------------
# Test: --@rules_latex//latex:_serve_cache_override short-circuits the
# implicit pipeline. With the flag set, a no-cache document must not
# produce a TectonicPopulateCache action (the serve loop primes the
# snapshot itself, outside the action graph) and the TectonicCompile
# action's argv must reference the override path.
# -----------------------------------------------------------------------------

def _serve_cache_override_test_impl(ctx):
    env = analysistest.begin(ctx)
    actions = analysistest.target_actions(env)
    asserts.false(
        env,
        _has_action_with_mnemonic(actions, "TectonicPopulateCache"),
        "expected NO TectonicPopulateCache action when " +
        "_serve_cache_override is set",
    )
    asserts.true(
        env,
        _has_action_with_mnemonic(actions, "TectonicCompile"),
        "expected TectonicCompile action",
    )

    # The override path must appear in the compile action's argv
    # as the cache tarball.
    compile_action = None
    for a in actions:
        if a.mnemonic == "TectonicCompile":
            compile_action = a
            break
    if compile_action == None:
        return analysistest.end(env)
    argv = compile_action.argv
    found = False
    expected = "/tmp/serve_cache_override_test_path.tar.gz"
    for i, arg in enumerate(argv):
        if arg == "--cache-tarball" and i + 1 < len(argv):
            if argv[i + 1] == expected:
                found = True
                break
    asserts.true(
        env,
        found,
        ("expected --cache-tarball {} in TectonicCompile argv " +
         "(serve override path); got: {}").format(expected, argv),
    )
    return analysistest.end(env)

serve_cache_override_test = analysistest.make(
    _serve_cache_override_test_impl,
    config_settings = {
        # buildifier: disable=canonical-repository
        "@@//latex:_serve_cache_override": "/tmp/serve_cache_override_test_path.tar.gz",
    },
)

# -----------------------------------------------------------------------------
# Test: a directory-shaped override path (no .tar.gz suffix) routes to
# --cache-dir instead of --cache-tarball, enabling the
# pre-extracted-cache fast-path.
# -----------------------------------------------------------------------------

def _serve_cache_override_dir_test_impl(ctx):
    env = analysistest.begin(ctx)
    actions = analysistest.target_actions(env)
    asserts.false(
        env,
        _has_action_with_mnemonic(actions, "TectonicPopulateCache"),
        "expected NO TectonicPopulateCache action when " +
        "_serve_cache_override is set to a dir-shaped path",
    )
    compile_action = None
    for a in actions:
        if a.mnemonic == "TectonicCompile":
            compile_action = a
            break
    if compile_action == None:
        return analysistest.end(env)
    argv = compile_action.argv
    expected = "/tmp/serve_cache_override_test_dir"
    found_dir = False
    found_tarball = False
    for i, arg in enumerate(argv):
        if arg == "--cache-dir" and i + 1 < len(argv):
            if argv[i + 1] == expected:
                found_dir = True
        if arg == "--cache-tarball" and i + 1 < len(argv):
            if argv[i + 1] == expected:
                found_tarball = True
    asserts.true(
        env,
        found_dir,
        ("expected --cache-dir {} in TectonicCompile argv " +
         "(directory-shaped override should route to --cache-dir); " +
         "got: {}").format(expected, argv),
    )
    asserts.false(
        env,
        found_tarball,
        "directory-shaped override must NOT route to --cache-tarball",
    )
    return analysistest.end(env)

serve_cache_override_dir_test = analysistest.make(
    _serve_cache_override_dir_test_impl,
    config_settings = {
        # buildifier: disable=canonical-repository
        "@@//latex:_serve_cache_override": "/tmp/serve_cache_override_test_dir",
    },
)

# Note: we used to have a `compile_supports_workers_test` here that
# asserted the persistent-worker exec requirements appear on the
# TectonicCompile action. Starlark's analysis-test API doesn't
# expose `execution_requirements` on an `Action`, so the assertion
# can't be expressed at analysis time. The empirical check lives in
# the example workspace's `bazel build` output ("1 worker"), and the
# wiring is small enough (two key-value pairs in
# `execution_requirements` plus a param-file shim) that further test
# coverage isn't warranted.

# -----------------------------------------------------------------------------
# Test: --@rules_latex//latex:_serve_cache_override does NOT override a
# user-supplied `cache = "..."`. The explicit user choice must always
# win, including under serve mode.
# -----------------------------------------------------------------------------

def _serve_cache_override_respects_user_cache_test_impl(ctx):
    env = analysistest.begin(ctx)
    actions = analysistest.target_actions(env)
    compile_action = None
    for a in actions:
        if a.mnemonic == "TectonicCompile":
            compile_action = a
            break
    asserts.true(
        env,
        compile_action != None,
        "expected TectonicCompile action",
    )
    if compile_action == None:
        return analysistest.end(env)

    # The argv must reference the user's fake cache, NOT the
    # override path. We look for --cache-tarball and confirm its
    # value is not the override.
    override_path = "/tmp/serve_cache_override_must_not_win.tar.gz"
    argv = compile_action.argv
    cache_arg_value = None
    for i, arg in enumerate(argv):
        if arg == "--cache-tarball" and i + 1 < len(argv):
            cache_arg_value = argv[i + 1]
            break
    asserts.true(
        env,
        cache_arg_value != None,
        "expected --cache-tarball in TectonicCompile argv",
    )
    asserts.true(
        env,
        cache_arg_value != override_path,
        ("serve cache override path {} leaked into a target with " +
         "explicit cache=; got argv: {}").format(override_path, argv),
    )
    return analysistest.end(env)

serve_cache_override_respects_user_cache_test = analysistest.make(
    _serve_cache_override_respects_user_cache_test_impl,
    config_settings = {
        # buildifier: disable=canonical-repository
        "@@//latex:_serve_cache_override": "/tmp/serve_cache_override_must_not_win.tar.gz",
    },
)

# -----------------------------------------------------------------------------
# Test: action-schema canary
# -----------------------------------------------------------------------------
#
# Snapshots both:
#
#   1. The set of declared output basenames on a canonical
#      latex_document target (synctex + implicit pipeline, the most
#      output-heavy default config). Changing the rule's output set
#      drifts this snapshot, failing the test and signalling that
#      `_EXPECTED_ACTION_SCHEMA` in latex/private/action_schema.bzl
#      also needs bumping (see DESIGN.md §5 item 10 for why).
#
#   2. That the schema env var is actually plumbed through to the
#      populate-cache and compile actions. Catches an "oh I added a
#      third action and forgot the env" regression directly.
#
# The "rename the schema constant" half is enforced by code review:
# the diff that updates the expected basenames here is the same diff
# that should bump action_schema.bzl, and any reviewer will catch a
# half-update.

def _action_schema_canary_test_impl(ctx):
    env = analysistest.begin(ctx)
    actions = analysistest.target_actions(env)

    # Collect every declared output across every action this target
    # registers. Sorted + de-duped: the same File can in principle
    # appear under multiple actions (e.g. via output groups), and
    # we don't want that to break the snapshot ordering. Starlark
    # has no set comprehension; we walk a dict to dedupe.
    dedup = {}
    for action in actions:
        for out in action.outputs.to_list():
            dedup[out.basename] = True
    basenames = sorted(dedup.keys())

    # Tied to the `_doc_synctex_canary` target's config:
    #   synctex = True      -> .synctex.gz exists
    #   no cache attr       -> implicit pipeline runs the intermediate
    #                          cache tarball
    expected = sorted([
        "_doc_synctex_canary.pdf",
        "_doc_synctex_canary.synctex.gz",
        # The implicit pipeline's intermediate cache tarball.
        "__doc_synctex_canary_implicit_cache.tar.gz",
        # One-line shell shim the compile action wraps around
        # python3 + tectonic_compile.py so Bazel's worker strategy
        # has a single exec path to identify the worker by. See
        # the `args.use_param_file(...)` block in
        # latex_document.bzl _compile_action.
        "__doc_synctex_canary_compile_shim.sh",
    ])
    asserts.equals(
        env,
        expected,
        basenames,
        (
            "latex_document declared-output set drifted from the " +
            "canary snapshot. If you intended to add/remove outputs, " +
            "ALSO bump _EXPECTED_ACTION_SCHEMA in " +
            "latex/private/action_schema.bzl so existing action-cache " +
            "entries get invalidated (DESIGN.md §5 item 10)."
        ),
    )

    # Schema env var must be present in the populate-cache and
    # compile actions — that's how the cache-busting actually works.
    relevant = [
        a
        for a in actions
        if a.mnemonic in ("TectonicPopulateCache", "TectonicCompile")
    ]
    asserts.true(
        env,
        len(relevant) >= 2,
        ("expected both TectonicPopulateCache and TectonicCompile " +
         "actions on the canary target; got: " +
         ", ".join([a.mnemonic for a in actions])),
    )
    for a in relevant:
        asserts.true(
            env,
            a.env.get("RULES_LATEX_ACTION_SCHEMA", "") == _EXPECTED_ACTION_SCHEMA,
            (
                "RULES_LATEX_ACTION_SCHEMA missing or mismatched in " +
                "env of " + a.mnemonic + " action. Expected '" +
                _EXPECTED_ACTION_SCHEMA + "', got '" +
                str(a.env.get("RULES_LATEX_ACTION_SCHEMA", "<unset>")) +
                "'. See latex/private/action_schema.bzl."
            ),
        )

    return analysistest.end(env)

action_schema_canary_test = analysistest.make(_action_schema_canary_test_impl)

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

    # Fake bib file for the pkg_files test. Genrule output rather than
    # a real source so we can declare it inline in this file.
    native.genrule(
        name = "_pkg_files_bib",
        outs = ["_pkg_files_bib.bib"],
        cmd = "echo '% fake bib' > $@",
    )
    latex_document(
        name = "_doc_pkg_files",
        main = "_test_doc.tex",
        srcs = [":_test_doc_tex"],
        pkg_files = {":_pkg_files_bib": "refs.bib"},
        tags = ["manual"],
    )

    # Canary target for the action-schema snapshot. synctex = True
    # so the .synctex.gz output is present; no cache attr so the
    # implicit pipeline contributes its intermediate tarball. This
    # is the most output-heavy default configuration; pinning it
    # catches any future change to the declared-output set.
    latex_document(
        name = "_doc_synctex_canary",
        main = "_test_doc.tex",
        srcs = [":_test_doc_tex"],
        synctex = True,
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
    pkg_files_test(
        name = "pkg_files_test",
        target_under_test = ":_doc_pkg_files",
    )
    serve_cache_override_test(
        name = "serve_cache_override_test",
        target_under_test = ":_doc_implicit",
    )
    serve_cache_override_dir_test(
        name = "serve_cache_override_dir_test",
        target_under_test = ":_doc_implicit",
    )
    serve_cache_override_respects_user_cache_test(
        name = "serve_cache_override_respects_user_cache_test",
        target_under_test = ":_doc_with_cache",
    )
    action_schema_canary_test(
        name = "action_schema_canary_test",
        target_under_test = ":_doc_synctex_canary",
    )

    native.test_suite(
        name = name,
        tests = [
            ":implicit_pipeline_test",
            ":checked_in_cache_test",
            ":synctex_output_test",
            ":no_synctex_test",
            ":pkg_files_test",
            ":serve_cache_override_test",
            ":serve_cache_override_dir_test",
            ":serve_cache_override_respects_user_cache_test",
            ":action_schema_canary_test",
        ],
    )
