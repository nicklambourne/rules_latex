# CTAN test fixtures

These are pinned snapshots of CTAN package zips, served by a local
HTTP server during CI so the `//tests/ctan:*` tests don't depend on
mirror availability.

## Layout

The directory tree mirrors the URL paths that
[`tools/tectonic_populate_cache.py`](../../../tools/tectonic_populate_cache.py)
requests, so a vanilla `python3 -m http.server` rooted here serves
them at the same paths CTAN does.

| Package | Fixture path | Source |
|---|---|---|
| `lipsum` | `macros/latex/contrib/lipsum.zip` | real CTAN |
| `test-pkg-a` | `macros/latex/contrib/test-pkg-a.zip` | synthetic — see below |
| `test-pkg-b` | `macros/latex/contrib/test-pkg-b.zip` | synthetic — see below |

`test-pkg-a` and `test-pkg-b` are *not* real CTAN packages. They're
hand-built minimal `.sty` files that exercise the auto-resolver
end-to-end: `test-pkg-a.sty` does `\RequirePackage{test-pkg-b}`, so
listing only `test-pkg-a` in `ctan_packages` and getting a successful
compile proves both that the resolver discovered `test-pkg-b` and
that the fetched files were actually consumable by tectonic. See the
`transitive_resolve_test` target in `tests/ctan/BUILD.bazel`.

## How CI uses them

The `Test` job in `.github/workflows/ci.yml` starts a Python HTTP
server rooted at this directory, sets
`RULES_LATEX_CTAN_MIRROR=http://localhost:<port>` for the test
invocation, and shuts it down after. The mirror env var is read by
[`tectonic_populate_cache.py`](../../../tools/tectonic_populate_cache.py)
and substituted into the URL prefix.

## Refreshing the fixtures

When a real-CTAN fixture goes stale (CTAN updated the package and a
test needs the new version), re-download from CTAN:

```bash
curl -sSL -o tests/ctan/fixtures/macros/latex/contrib/lipsum.zip \
    https://mirrors.ctan.org/macros/latex/contrib/lipsum.zip
```

Sanity-check the result is a real zip:

```bash
file tests/ctan/fixtures/macros/latex/contrib/*.zip
```

The `test-pkg-a` / `test-pkg-b` synthetic fixtures don't need
refreshing — they're stable and intentionally minimal. To rebuild
them from scratch (e.g. if the on-disk zips get corrupted):

```bash
mkdir -p /tmp/synth/{test-pkg-a,test-pkg-b}
cat > /tmp/synth/test-pkg-a/test-pkg-a.sty <<'EOF'
\NeedsTeXFormat{LaTeX2e}
\ProvidesPackage{test-pkg-a}[2026/05/23 v1.0 Synthetic test package A]
\RequirePackage{test-pkg-b}
\newcommand{\testpkga}{Hello from A}
\endinput
EOF
cat > /tmp/synth/test-pkg-b/test-pkg-b.sty <<'EOF'
\NeedsTeXFormat{LaTeX2e}
\ProvidesPackage{test-pkg-b}[2026/05/23 v1.0 Synthetic test package B]
\newcommand{\testpkgb}{Hello from B}
\endinput
EOF
cd /tmp/synth && zip -r .../test-pkg-a.zip test-pkg-a/ && zip -r .../test-pkg-b.zip test-pkg-b/
```

Local-dev users running `bazel test //tests/ctan:*` *without*
`RULES_LATEX_CTAN_MIRROR` set will fail on `transitive_resolve_test`
(real CTAN doesn't host `test-pkg-a`). The fixture server is
mandatory for that test; CI sets it up automatically.
