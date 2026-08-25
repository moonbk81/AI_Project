"""The browser UI's markdown renderer, exercised through node.

Answers quote log lines verbatim, so escaping is a security property rather
than a formatting nicety — worth a test even though the code is JavaScript.
"""

import json
import shutil
import subprocess

import pytest

MODULE = "backend/static/js/markdown.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def render(source: str) -> str:
    script = (
        f'import {{ renderMarkdown }} from "./{MODULE}";'
        f"process.stdout.write(renderMarkdown({json.dumps(source)}));"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_html_in_a_log_line_is_shown_not_executed():
    html = render("로그: <img src=x onerror=alert(1)> <script>alert(2)</script>")

    assert "&lt;script&gt;" in html and "&lt;img" in html
    assert "<script>" not in html and "onerror=alert" not in html.replace("&lt;img src=x onerror=alert(1)&gt;", "")


def test_code_fences_keep_their_content_escaped():
    html = render("```\n<b>not bold</b>\n```")

    assert html.startswith("<pre><code>") and "&lt;b&gt;not bold&lt;/b&gt;" in html


def test_the_shapes_answers_actually_use():
    assert render("## 원인") == "<h4>원인</h4>"
    assert render("**핵심:** 종료") == "<p><strong>핵심:</strong> 종료</p>"
    assert render("- 첫째\n- 둘째") == "<ul>\n<li>첫째</li>\n<li>둘째</li>\n</ul>"
    assert render("> 과거 기록") == "<blockquote>과거 기록</blockquote>"
    assert render("`CODE`") == "<p><code>CODE</code></p>"


def test_empty_input_renders_nothing():
    assert render("") == ""
