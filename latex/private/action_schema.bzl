"""Rule output-schema version used as a cache-busting input.

Bazel's action cache key is computed from the action's inputs,
command line, and environment — *not* from the set of declared
outputs. So if a `latex_document` (or any rule) gains a new
declared output, an existing cache entry from before the change
can satisfy the new action even though the cached blob is missing
the new output. We've been bitten by this once: the synctex case
(commit `0d4639c`, papered over with a manual cache-bust suffix;
the real symptom was fixed by `1978f5d`).

The defensive fix is to bake a *schema version* into the action's
env. Bumping this constant forces every cache key derived from it
to change, which invalidates any pre-existing entries that were
keyed off the old output schema. So when a rule changes its
declared-output set, bump the version here as well.

Forgotten bumps are caught by
`tests/starlark:action_schema_canary_test`: it snapshots the
declared output set of a canonical `latex_document` configuration
and fails when the snapshot drifts. The failure message points
the developer back here.

DESIGN.md §5 item 10 for the historical context.
"""

# Bump this any time a rule under `latex/private/` changes the
# *set* of declared outputs (renames or new optional outputs).
# Pure content-of-output changes (e.g. tectonic version bumps) do
# NOT need a bump — Bazel's existing input-hashing catches those.
RULES_LATEX_ACTION_SCHEMA = "v1"
