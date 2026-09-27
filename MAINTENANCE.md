# Dependency maintenance

The root `MODULE.bazel.lock` is generated with Bazel 8.0.0, committed,
and checked in CI with `--lockfile_mode=error`. Other CI matrix versions
use `--lockfile_mode=off` because Bazel lock formats differ. This is the
rules_latex development repository's lockfile; a consuming root module
resolves its own dependency graph and lockfile.

Dependabot proposes weekly Bazel module and GitHub Actions updates.
The pinned Tectonic, biber, TeX bundle, and PDF.js archives are defined
in Starlark and require a manual review at least once per release:

1. Check upstream release notes and security advisories. Update URLs,
   versions, and verified hashes in the same PR.
2. Update biber and bundle biblatex together; compile the paper/thesis
   bibliography examples on Linux x86_64, Linux ARM64, and macOS.
3. For PDF.js, run the browser smoke and JavaScript unit suites, checking
   text, image, and font rendering after rebuilds.
4. Rebuild cache snapshots when bundle content changes; confirm offline
   examples and the rules_python consumer fixtures pass.
5. Compare representative cold build, warm build, and live save-to-visible
   timings before merging. Explain any regression in the update PR.

The documentation Python lock is refreshed separately from
`docs/requirements.in` and installed with hash verification in CI.
