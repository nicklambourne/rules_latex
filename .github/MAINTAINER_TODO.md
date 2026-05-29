# Maintainer TODO

Internal punch list — not user-facing. Not rendered to the docs site
(mkdocs only serves `docs/site/`), not surfaced in the README, not
linked from anywhere user-readable. Keep entries terse; remove once
done.

## Open

### README: capture a `latex_live` screenshot

Drop a PNG or GIF of the in-browser preview into `./assets/serve-web.png`
(or `serve-web.gif` if you want motion) and embed it at the top of the
**Live preview** section in `README.md`, just under the `### Live preview`
heading:

```html
<p align="center"><img src="./assets/serve-web.png" alt="latex_live preview" width="700" /></p>
```

The CV example is the most photogenic target:

```bash
cd examples && bazel run //cv:cv_live
# → http://127.0.0.1:8767/
```

Background: scaffolded in [#21](https://github.com/nicklambourne/rules_latex/pull/21);
the TODO comment used to live in the README itself but was moved here
so it isn't visible to users.
