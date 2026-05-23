# CTAN test fixtures

These are pinned snapshots of CTAN package zips, served by a local
HTTP server during CI so the `//tests/ctan:*` tests don't depend on
mirror availability.

## Layout

The directory tree mirrors the URL paths that
[`tools/tectonic_populate_cache.py`](../../../tools/tectonic_populate_cache.py)
requests, so a vanilla `python3 -m http.server` rooted here serves
them at the same paths CTAN does.

| Package | Fixture path |
|---|---|
| `lipsum` | `macros/latex/contrib/lipsum.zip` |
| `biblatex-apa` | `macros/latex/contrib/biblatex-contrib/biblatex-apa.zip` |

## How CI uses them

The `Test` job in `.github/workflows/ci.yml` starts a Python HTTP
server rooted at this directory, sets
`RULES_LATEX_CTAN_MIRROR=http://localhost:<port>` for the test
invocation, and shuts it down after. The mirror env var is read by
[`tectonic_populate_cache.py`](../../../tools/tectonic_populate_cache.py)
and substituted into the URL prefix.

## Refreshing the fixtures

When a fixture goes stale (CTAN updated the package and a test
needs the new version), re-download from CTAN:

```bash
curl -sSL -o tests/ctan/fixtures/macros/latex/contrib/lipsum.zip \
    https://mirrors.ctan.org/macros/latex/contrib/lipsum.zip

curl -sSL -o tests/ctan/fixtures/macros/latex/contrib/biblatex-contrib/biblatex-apa.zip \
    https://mirrors.ctan.org/macros/latex/contrib/biblatex-contrib/biblatex-apa.zip
```

Sanity-check the result is a real zip:

```bash
file tests/ctan/fixtures/macros/latex/contrib/**/*.zip
```

Then commit. Local-dev users running `bazel test //tests/ctan:*`
*without* `RULES_LATEX_CTAN_MIRROR` set still hit real CTAN —
fixtures are purely a CI flake-resistance layer.
