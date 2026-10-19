#!/usr/bin/env python3
"""Fail if a closed game was changed.

A game is closed once games/<id>/result.json exists. Two checks:
  1. --base REF: no file of a game that was already closed at REF may differ
     between REF and the working tree (catches edits before a game is tagged).
  2. Every tag game/<id> must match games/<id>/ in the working tree exactly.

Usage: check_frozen.py [--base REF]
"""

import argparse
import os
import subprocess
import sys

GAMES = "games"


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def commit_exists(ref: str) -> bool:
    return subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], capture_output=True
    ).returncode == 0


def lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line]


def changed_since(ref: str, path: str) -> list[str]:
    """Files under `path` that differ between `ref` and the working tree, including new ones."""
    tracked = lines(git("diff", "--name-only", ref, "--", path))
    untracked = lines(git("ls-files", "--others", "--exclude-standard", "--", path))
    return tracked + untracked


def closed_at(ref: str) -> list[str]:
    files = lines(git("ls-tree", "-r", "--name-only", ref, "--", GAMES))
    return sorted(f.split("/")[1] for f in files if f.count("/") == 2 and f.endswith("/result.json"))


def check(base: str | None) -> list[str]:
    problems = []
    if base:
        if commit_exists(base):
            for game in closed_at(base):
                problems += [f"{f} (game {game} was closed at {base[:12]})"
                             for f in changed_since(base, f"{GAMES}/{game}/")]
        else:
            print(f"warning: base {base!r} is not a known commit, skipping the base check", file=sys.stderr)
    for tag in lines(git("tag", "--list", "game/*")):
        game = tag.removeprefix("game/")
        problems += [f"{f} (differs from tag {tag})" for f in changed_since(tag, f"{GAMES}/{game}/")]
    return sorted(set(problems))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", help="commit to compare against, e.g. HEAD or the previous push")
    args = parser.parse_args(argv)
    os.chdir(git("rev-parse", "--show-toplevel").strip())
    problems = check(args.base)
    if problems:
        print("Closed games must not be changed:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    print("OK: no closed game was changed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
