import json
import os
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "extract_instagram_post.py"
URL = "https://www.instagram.com/p/Example123/"


def _run(*args, env=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], text=True, capture_output=True, env=env, check=False,
    )


def test_requires_post_url():
    result = _run("https://www.instagram.com/explore/tags/finance/")
    assert result.returncode == 1
    assert "post or reel permalink" in result.stderr


def test_script_reads_one_visible_post_and_stops_on_access_wall(tmp_path):
    cdp = tmp_path / "cdp"
    cdp.write_text(
        "#!" + sys.executable + "\n"
        "import json, os, sys\n"
        "args = sys.argv[1:]\n"
        "if 'pages' in args:\n"
        f"    data = {{'ok': True, 'pages': [{{'id': 'target1', 'type': 'page', 'url': {URL!r}}}]}}\n"
        "elif 'stop-state' in args:\n"
        "    blocked = os.environ.get('TEST_BLOCKED') == '1'\n"
        "    data = {'ok': True, 'classification_ok': True, 'status': 'blocked' if blocked else 'ok', 'target': {'id': 'target1'}}\n"
        "elif 'text' in args:\n"
        "    data = {'ok': True, 'items': [{'text': 'A synthetic finance caption'}]}\n"
        "else:\n"
        "    data = {'ok': True, 'result': {'value': {'status': 'no_scoped_time'}}}\n"
        "print(json.dumps(data))\n",
        encoding="utf-8",
    )
    cdp.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}"}
    result = _run(URL, env=env)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["posts"] == [{"source_url": URL, "text": "A synthetic finance caption"}]
    blocked = _run(URL, env={**env, "TEST_BLOCKED": "1"})
    assert blocked.returncode == 1
    assert blocked.stdout == ""
    assert "access boundary" in blocked.stderr
