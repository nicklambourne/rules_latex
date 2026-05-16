# `.bcr/` — Bazel Central Registry templates

Templates consumed by [publish-to-bcr][p2b] when we cut a `rules_latex`
release. The reusable workflow at
[`.github/workflows/publish.yml`](../.github/workflows/publish.yml)
glues them together.

| File                       | Purpose                                                 |
|----------------------------|---------------------------------------------------------|
| `config.yml`               | publish-to-bcr config (currently empty; defaults are correct). |
| `metadata.template.json`   | The `metadata.json` that lands in `modules/rules_latex/metadata.json` in the BCR. |
| `presubmit.yml`            | Tests the BCR runs against this submission. Points at our `examples/` test module. |
| `source.template.json`     | URL + integrity template for the source archive. publish-to-bcr fills the placeholders at release time. Also points at `docs_url` for the Stardoc binaryprotos. |

## Pipeline

1. **`git push --tag vX.Y.Z`** triggers
   [`.github/workflows/release.yml`](../.github/workflows/release.yml),
   which calls the reusable
   [`bazel-contrib/.github/release_ruleset`](https://github.com/bazel-contrib/.github/blob/master/.github/workflows/release_ruleset.yaml)
   workflow. That workflow:
   - Runs `bazel test //... //tests/...`.
   - Runs
     [`release_prep.sh`](../.github/workflows/release_prep.sh), which
     produces `rules_latex-X.Y.Z.tar.gz` (source) and
     `rules_latex-X.Y.Z.docs.tar.gz` (Stardoc binaryprotos).
   - Generates SLSA build provenance attestations over both archives
     (`<filename>.intoto.jsonl`).
   - Publishes a GitHub Release.
2. The Release workflow's `publish-to-bcr` job then chains into
   [`.github/workflows/publish.yml`](../.github/workflows/publish.yml),
   which invokes the reusable
   [`bazel-contrib/publish-to-bcr`](https://github.com/bazel-contrib/publish-to-bcr)
   workflow. That:
   - Reads the templates in this directory.
   - Hydrates `source.json` and `MODULE.bazel` for the new version.
   - Pushes a branch to our fork of
     [`bazelbuild/bazel-central-registry`](https://github.com/bazelbuild/bazel-central-registry)
     and opens a PR.
   - Includes the attestations from step 1 alongside the BCR module
     files (see
     [BCR discussion #2721](https://github.com/bazelbuild/bazel-central-registry/discussions/2721)).

## One-time setup (release engineering)

Before the first publish:

1. **Fork `bazelbuild/bazel-central-registry`** to the same account
   that owns `rules_latex` (i.e. `nicklambourne`). The fork name in
   `publish.yml` is `nicklambourne/bazel-central-registry`.
2. **Create a personal access token** (classic, with `repo` and
   `workflow` scopes). Store it as the repo secret `BCR_PUBLISH_TOKEN`
   in `rules_latex`'s settings. See
   [the publish-to-bcr README][p2b-token] for token guidance.

Until both are done, the `publish-to-bcr` job fails with an
authentication error — but the release itself still ships, so the
recovery is just to retry the publish workflow via
`workflow_dispatch` after the secret is in place.

## Stardoc on the BCR

`source.template.json` includes a `docs_url` field pointing at the
Stardoc archive. The BCR's registry UI consumes it to render API
reference pages alongside the module metadata. See
[bazel-central-registry/docs/stardoc.md][stardoc-doc] and
[Aspect's announcement][aspect-stardoc] for details.

[p2b]: https://github.com/bazel-contrib/publish-to-bcr
[p2b-token]: https://github.com/bazel-contrib/publish-to-bcr#3-create-a-personal-access-token
[stardoc-doc]: https://github.com/bazelbuild/bazel-central-registry/blob/main/docs/stardoc.md
[aspect-stardoc]: https://blog.aspect.build/stardocs-on-bcr
