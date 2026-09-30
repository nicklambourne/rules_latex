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
2. After the GitHub Release is available, a maintainer manually dispatches
   [`.github/workflows/publish.yml`](../.github/workflows/publish.yml) with the
   release tag. The Release workflow contains a disabled chaining hook for
   re-enabling this step automatically later. The publish workflow invokes the
   reusable
   [`bazel-contrib/publish-to-bcr`](https://github.com/bazel-contrib/publish-to-bcr)
   workflow, which:
   - Reads the templates in this directory.
   - Hydrates `source.json` and `MODULE.bazel` for the new version.
   - Pushes a branch to our fork of
     [`bazelbuild/bazel-central-registry`](https://github.com/bazelbuild/bazel-central-registry)
     and opens a PR.
   - Includes the attestations from step 1 alongside the BCR module
     files (see
     [BCR discussion #2721](https://github.com/bazelbuild/bazel-central-registry/discussions/2721)).

## Release-engineering configuration

The repository and registry fork are already configured, and published
releases are listed on the [BCR module page][bcr-module]. The working setup is:

1. **Registry fork.** `bazelbuild/bazel-central-registry` is forked to the
   account that owns `rules_latex` (i.e. `nicklambourne`). The fork name in
   `publish.yml` is `nicklambourne/bazel-central-registry`.
2. **Publish token.** The repo secret `BCR_PUBLISH_TOKEN` stores a personal
   access token (classic, with `repo` and `workflow` scopes). See
   [the publish-to-bcr README][p2b-token] for token guidance.

If the fork or token changes, the publish job fails independently of the
already-completed release. Repair the configuration and retry the publish
workflow with `workflow_dispatch`; no replacement release is needed.

## Signing-workflow version tags

The two reusable signing workflows must use full version tags (`vX.Y.Z`),
currently `release_ruleset.yaml@v7.7.0` and `publish.yaml@v1.2.0`.
These are deliberate exceptions to commit-SHA pinning: BCR's `slsa-verifier`
rejects SHA references in signing certificates even when signatures and artifact
hashes are valid. Keep other action pins unchanged. See
[the verifier's restriction](https://github.com/slsa-framework/slsa-verifier/issues/12).

Version tags can move. Before releasing, check that these tags still resolve to
reviewed commits; investigate unexpected movement rather than publishing:

- `bazel-contrib/.github@v7.7.0`: `1d798ff015ed0696433e01e2c3ccbb2abefadad7`
- `bazel-contrib/publish-to-bcr@v1.2.0`: `0a23c53c2baffdaf2ce8dd23c2c0e45eb3b79093`

Update these reviewed identities when intentionally upgrading a workflow.

Before dispatching BCR publication, download the release archive and its
`.intoto.jsonl` bundle and verify them with the version of `slsa-verifier` used
by BCR (currently 2.7.1):

```sh
slsa-verifier verify-github-attestation \
  --attestation-path rules_latex-X.Y.Z.tar.gz.intoto.jsonl \
  --source-uri github.com/nicklambourne/rules_latex \
  --builder-id https://github.com/bazel-contrib/.github/.github/workflows/release_ruleset.yaml@v7.7.0 \
  rules_latex-X.Y.Z.tar.gz
```

Repeat for the docs archive. After publication creates the BCR PR, verify its
exact `MODULE.bazel` and `source.json` files against their uploaded bundles,
using `https://github.com/bazel-contrib/publish-to-bcr/.github/workflows/publish.yaml@v1.2.0`
as the builder ID. Require the BCR checks to pass before considering registry
publication complete. `gh attestation verify` is useful additional verification,
but does not enforce the same builder-reference policy and is not a substitute.

A failed verification is not a reason to move a published release tag, replace
its archives, or remove attestations. Normal publication retries may overwrite
attestation assets and the BCR branch; inspect the failure before retrying.

## Stardoc on the BCR

`source.template.json` includes a `docs_url` field pointing at the
Stardoc archive. The BCR's registry UI consumes it to render API
reference pages alongside the module metadata. See
[bazel-central-registry/docs/stardoc.md][stardoc-doc] and
[Aspect's announcement][aspect-stardoc] for details.

[p2b]: https://github.com/bazel-contrib/publish-to-bcr
[p2b-token]: https://github.com/bazel-contrib/publish-to-bcr#3-create-a-personal-access-token
[bcr-module]: https://registry.bazel.build/modules/rules_latex/
[stardoc-doc]: https://github.com/bazelbuild/bazel-central-registry/blob/main/docs/stardoc.md
[aspect-stardoc]: https://blog.aspect.build/stardocs-on-bcr
