import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import check_frozen  # noqa: E402


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def write(repo, path, text):
    p = repo / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    write(tmp_path, "games/2026-Q3/game.json", "{}")
    write(tmp_path, "games/2026-Q3/result.json", "{}")
    write(tmp_path, "games/2026-Q4/game.json", "{}")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "init")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_clean(repo):
    assert check_frozen.check("HEAD") == []


def test_running_game_may_change(repo):
    write(repo, "games/2026-Q4/prices.json", "{}")
    write(repo, "games/2026-Q4/result.json", "{}")  # closing a game is allowed
    assert check_frozen.check("HEAD") == []


@pytest.mark.parametrize("change", [
    lambda r: write(r, "games/2026-Q3/game.json", '{"x": 1}'),
    lambda r: write(r, "games/2026-Q3/extra.json", "{}"),
    lambda r: (r / "games/2026-Q3/result.json").unlink(),
])
def test_closed_game_change_is_caught_against_base(repo, change):
    change(repo)
    assert len(check_frozen.check("HEAD")) == 1


def test_committed_change_is_caught_against_tag(repo):
    git(repo, "tag", "game/2026-Q3")
    write(repo, "games/2026-Q3/game.json", '{"x": 1}')
    git(repo, "commit", "-q", "-am", "sneaky")
    # Not visible against the latest commit, but against the tag.
    assert check_frozen.check(None) == ["games/2026-Q3/game.json (differs from tag game/2026-Q3)"]


def test_unknown_base_is_skipped(repo):
    assert check_frozen.check("0" * 40) == []
