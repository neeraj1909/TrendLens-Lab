import json
import importlib.util
import os
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "search_instagram_posts.py"


def _run(*args, env=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=env, check=False,
    )


def test_authorization_flag_removed_and_target_and_attempt_bounds():
    assert _run("finance", "--authorized", "--limit", "6").returncode != 0
    assert _run("finance", "--target-readable", "0").returncode != 0
    assert _run("finance", "--target-readable", "51").returncode != 0
    assert _run("finance", "--max-attempts", "101").returncode != 0


def test_partial_result_is_printed_with_nonzero_status(monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("instagram_search_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "search_and_collect_public_posts", lambda *_a, **_k: {
        "stop_reason": "login_required", "candidates": [
            {"status": "read", "post_text": "A plain finance tip", "search_result_text": "#finance"},
            {"status": "unavailable", "search_result_text": "#finance"},
            {"status": "read", "post_text": "   ", "search_result_text": "#finance"},
        ],
    })
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "#finance"])
    assert module.main() == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["stop_reason"] == "login_required"
    assert payload["sample_summary"] == {
        "search_phrase": "#finance",
        "candidate_count": 3,
        "attempted_candidate_count": 3,
        "target_readable_count": 25,
        "successfully_read_nonempty_text_count": 1,
        "target_met": False,
        "literal_visible_text_match_count": 0,
        "match_denominator": 1,
        "elapsed_seconds": None,
        "pages_visited": None,
        "stop_reason": "login_required",
        "note": payload["sample_summary"]["note"],
    }
    assert "Google snippets and URLs are excluded" in payload["sample_summary"]["note"]
    assert "platform totals" in payload["sample_summary"]["note"]


def test_search_automation_with_fake_cdp_and_access_boundary(tmp_path):
    cdp = tmp_path / "cdp"
    cdp.write_text(
        "#!" + sys.executable + "\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "if 'close' in args:\n"
        "    Path(os.environ['TEST_CLOSE_MARKER']).write_text('closed')\n"
        "    data = {'ok': True, 'target_gone': True}\n"
        "elif 'open' in args:\n"
        "    data = {'ok': True, 'page': {'id': 'owned'}}\n"
        "elif 'stop-state' in args:\n"
        "    data = {'ok': True, 'classification_ok': True, 'status': 'blocked' if os.environ.get('TEST_BLOCKED') else 'ok', 'target': {'id': 'owned'}}\n"
        "elif 'pages' in args:\n"
        "    data = {'ok': True, 'pages': [{'id': 'other', 'type': 'page', 'url': 'https://www.instagram.com/p/Example/'}, {'id': 'owned', 'type': 'page', 'url': 'https://www.instagram.com/p/Example/'}]}\n"
        "elif 'text' in args:\n"
        "    data = {'ok': True, 'items': [{'text': 'Synthetic finance caption'}]}\n"
        "elif 'eval' in args and any('const anchors' in arg for arg in args):\n"
        "    data = {'ok': True, 'result': {'value': [{'href': 'https://www.instagram.com/p/Example/', 'result_text': 'unverified snippet'}]}}\n"
        "elif 'eval' in args:\n"
        "    data = {'ok': True, 'result': {'value': {'status': 'no_scoped_time'}}}\n"
        "else:\n"
        "    data = {'ok': True}\n"
        "print(json.dumps(data))\n",
        encoding="utf-8",
    )
    cdp.chmod(0o755)
    marker = tmp_path / "closed"
    env = {**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}",
           "TEST_CLOSE_MARKER": str(marker)}
    result = _run("#finance", "--limit", "1", "--include-posts", env=env)
    assert result.returncode == 2, result.stderr
    payload = json.loads(result.stdout)
    candidate = payload["candidates"][0]
    assert candidate["status"] == "unverified"
    summary = payload["sample_summary"]
    assert summary["candidate_count"] == 1
    assert summary["successfully_read_nonempty_text_count"] == 0
    assert summary["literal_visible_text_match_count"] == 0
    assert summary["match_denominator"] == 0
    assert summary["target_readable_count"] == 25
    assert summary["target_met"] is False
    assert "Google snippets and URLs are excluded" in summary["note"]
    assert marker.read_text() == "closed"

    marker.unlink()
    blocked = _run("finance", "--include-posts", env={**env, "TEST_BLOCKED": "1"})
    assert blocked.returncode == 1
    assert blocked.stdout == ""
    assert marker.read_text() == "closed"


def test_cli_passes_twenty_limit_and_reels_only(monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("instagram_search_cli_args", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    received = {}

    def search(keyword, **kwargs):
        received.update(keyword=keyword, **kwargs)
        return {"candidates": []}

    monkeypatch.setattr(module, "search_and_collect_public_posts", search)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "#travel", "--limit", "20", "--reels-only", "--recent-days", "30"])
    assert module.main() == 2
    capsys.readouterr()
    assert received == {
        "keyword": "#travel", "target_readable": 25, "max_attempts": 20,
        "max_pages": 10, "max_elapsed_seconds": 600,
        "reels_only": True, "recent_days": 30,
    }


def test_cli_rejects_recent_days_outside_one_to_365():
    assert _run("finance", "--recent-days", "0").returncode != 0
    assert _run("finance", "--recent-days", "366").returncode != 0


def test_cli_defaults_and_target_maximum_passthrough(monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("instagram_search_cli_target", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    received = {}

    def search(keyword, **kwargs):
        received.update(keyword=keyword, **kwargs)
        return {"candidates": [], "attempted_count": 0, "readable_count": 0,
                "elapsed_seconds": 1.25, "pages_visited": 1, "stop_reason": "no_more_results"}

    monkeypatch.setattr(module, "search_and_collect_public_posts", search)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "education", "--target-readable", "50"])
    assert module.main() == 2
    payload = json.loads(capsys.readouterr().out)
    assert received["target_readable"] == 50
    assert received["max_attempts"] == 100
    assert received["reels_only"] is True
    assert payload["sample_summary"]["target_readable_count"] == 50
    assert payload["sample_summary"]["attempted_candidate_count"] == 0
    assert payload["sample_summary"]["elapsed_seconds"] == 1.25
    assert payload["sample_summary"]["pages_visited"] == 1
    assert payload["sample_summary"]["target_met"] is False


def test_cli_returns_success_when_requested_readable_target_is_reached(monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("instagram_search_cli_success", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "search_and_collect_public_posts", lambda *_a, **_k: {
        "stop_reason": "target_reached",
        "attempted_count": 1,
        "readable_count": 1,
        "candidates": [{
            "status": "read", "post_text": "A finance tip", "source_association": "verified",
        }],
    })
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "finance", "--target-readable", "1"])
    assert module.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["sample_summary"]["target_met"] is True
    assert payload["sample_summary"]["stop_reason"] == "target_reached"


def test_cli_include_posts_disables_reels_only(monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("instagram_search_cli_mixed", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    received = {}
    monkeypatch.setattr(module, "search_and_collect_public_posts", lambda keyword, **kwargs: (
        received.update(keyword=keyword, **kwargs) or {"candidates": []}
    ))
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "finance", "--include-posts"])
    assert module.main() == 2
    capsys.readouterr()
    assert received["reels_only"] is False
