"""Shared definition for the hermetic stdlib unittest targets."""

load("@rules_python//python:py_test.bzl", "py_test")

_HERMETIC_CONFIG = {
    "@rules_python//python/config_settings:bootstrap_impl": "script",
}

def py_unittest(name, data, extra_srcs = []):
    py_test(
        name = name,
        srcs = [name + ".py"] + extra_srcs,
        config_settings = _HERMETIC_CONFIG,
        data = data,
        legacy_create_init = 0,
        python_version = "3.13",
    )
