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
| `source.template.json`     | URL + integrity template for the source archive. publish-to-bcr fills the placeholders at release time. |

## Status

The publish workflow is set up but **gated behind manual
`workflow_dispatch`** — it doesn't fire automatically on tag push.
When we're ready for our first BCR submission (likely v0.2.0+),
we'll:

1. Configure a personal access token (`BCR_PUBLISH_TOKEN`) per the
   [publish-to-bcr README][p2b-token].
2. Fork [bazelbuild/bazel-central-registry][bcr] under the same
   account.
3. Either trigger the workflow manually from the GitHub UI, or
   change `on:` to `workflow_call` and invoke from the release
   workflow.

Until then this scaffold lets us iterate on metadata/presubmit
without committing to actually pushing anything.

[p2b]: https://github.com/bazel-contrib/publish-to-bcr
[p2b-token]: https://github.com/bazel-contrib/publish-to-bcr#3-create-a-personal-access-token
[bcr]: https://github.com/bazelbuild/bazel-central-registry
