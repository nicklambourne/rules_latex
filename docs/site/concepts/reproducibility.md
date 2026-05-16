# Reproducibility

By default, Tectonic embeds the current wall-clock time in the PDF
metadata (creation date, modification date), so identical inputs
produce non-byte-identical PDFs. For most workflows this is fine; for
some (release archives, deterministic CI snapshots, content-addressed
distribution) it isn't.

## Enabling reproducible mode

```python
latex_document(
    name = "cv",
    main = "cv.tex",
    srcs = ["cv.tex"],
    reproducible = True,
)
```

When `reproducible = True`:

1. The action's environment includes `SOURCE_DATE_EPOCH=0`. Tectonic
   honours this and uses 1970-01-01 for the PDF's embedded
   creation/modification dates.
2. The tectonic invocation includes `-Z deterministic-mode`. This
   normalises a handful of other engine-level non-determinisms
   (random number generator seeds, etc.).

The combination is enough to produce **byte-identical PDFs across
clean builds**.

## Verifying it

CI verifies this on every push:

```bash
bazel build //:cv_reproducible
sha256sum bazel-bin/cv_reproducible.pdf
bazel clean --expunge
bazel build //:cv_reproducible
sha256sum bazel-bin/cv_reproducible.pdf
# (same)
```

See the
[CI workflow](https://github.com/nicklambourne/rules_latex/blob/master/.github/workflows/ci.yml)
for the exact step.

## Caveats

### Mutually exclusive with SyncTeX

Tectonic's `-Z deterministic-mode` deliberately disables SyncTeX
output, because SyncTeX records absolute filesystem paths in the
`.synctex.gz` file (sandbox execroot paths) which aren't stable
across machines.

If you try to set both:

```python
latex_document(
    name = "cv",
    reproducible = True,
    synctex = True,  # ERROR at analysis time
    ...
)
```

…the rule fails at analysis with an explanatory message. Pick one or
the other.

### Not byte-identical across rules_latex versions

A rules_latex point release that changes the bundled tectonic version
will produce different PDFs (different engine version → different
output). Hold rules_latex version constant across the builds you want
to compare.

### Embedded fonts can vary across platforms

In rare cases involving system fonts (e.g. when a document uses
`\setmainfont{Arial}` on macOS), the embedded font subset can differ
between macOS and Linux. Avoid `fontspec` system-font lookups in
documents that need to be byte-identical across platforms — stick
with Tectonic's bundled fonts.
