# rules_python consumer compatibility fixtures

These nested Bazel modules verify that a consumer's Python toolchain can
coexist with the private Python 3.13 runtime used by `rules_latex`:

- `python_311` uses Bazel 8.0, the minimum supported `rules_python` 1.9.2,
  and runs the consumer target on Python 3.11.
- `python_314` uses Bazel 9.1, raises the module graph to `rules_python`
  2.3.2, and runs the consumer target on Python 3.14. `rules_python` 2.3.2's
  MODULE file uses `flag_alias`, which Bazel 8 cannot parse.

Each fixture builds both its consumer-owned `py_binary` and
`@rules_latex//tools:tectonic_compile`. Run both fixtures with:

```bash
./tests/consumers/check.sh
```

Bzlmod selects one `rules_python` module version for the standard dependency
graph. A consumer that requests an older version is raised to `rules_latex`'s
1.9.2 floor; a consumer that requests a newer version raises `rules_latex` to
that version. The Python runtimes remain separate because every private
`rules_latex` executable requests Python 3.13 explicitly.
