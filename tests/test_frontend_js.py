"""Run the browser UI's JavaScript tests as part of the normal pytest run.

The frontend has no build step and no npm dependencies, and the tests keep it
that way: they use node's own runner (`node:test`), so there is nothing to
install. This wrapper exists so `pytest tests/` covers the frontend too --
otherwise a JS-only regression passes CI unnoticed.

Skips rather than fails when node is missing, so the Python suite still runs on
a machine without it.
"""

import glob
import os
import shutil
import subprocess

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JS_TEST_GLOB = os.path.join(_REPO_ROOT, "tests", "js", "*.test.mjs")


def test_frontend_js_suite():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed; skipping the browser UI tests")

    # Passing the directory makes node treat it as a module path, so the files
    # are expanded here rather than left to the runner's own discovery.
    test_files = sorted(glob.glob(_JS_TEST_GLOB))
    assert test_files, f"no frontend tests matched {_JS_TEST_GLOB}"

    result = subprocess.run(
        [node, "--test", *test_files],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        pytest.fail(
            "node --test failed\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )
