#!/usr/bin/env python3
"""Run the repository's pin-test screen as a child and reduce its exit to a three-valued outcome.

    outcome = run_screen(repo, pr, token=...)

`repo` is `OWNER/NAME`, `pr` the pull request's number, `token` a `GH_TOKEN`
the child authenticates with. The child is this repository's
`check_pin_tests.py`, spawned isolated (`-I`), in its own session and with an
allowlisted environment (GH_TOKEN, GH_REPO and PATH, HOME, LANG, LC_ALL) so
nothing ambient reaches it, and it never outlives its budget: on timeout the
whole process group is killed. The outcome is exactly one of:
  - exit 0: `clean` — no candidates, nothing unscreened;
  - exit 1 with at least one parseable `path:line: text` row: `candidates`
    (the child exits 1 only when it screened every file);
  - anything else — exit 1 without a row, exit 2 (any unscreened file,
    with or without rows), a signal, a timeout, any other non-zero exit —
    `could-not-screen` with a short human `detail`, the rows it found kept
    in `candidates` and each `could not screen <name>: <why>` stderr line
    parsed into `unscreened`.
A run that screened nothing is never `clean`; the candidates are judged by a
reader, never consumed as a verdict.
"""
import os
import re
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ROW = re.compile(r'^(.+?):(\d+): (.*)$')
ENV_NAMES = ('PATH', 'HOME', 'LANG', 'LC_ALL')


@dataclass(frozen=True)
class ScreenOutcome:
    status: str                       # 'clean' | 'candidates' | 'could-not-screen'
    candidates: tuple[tuple[str, int, str], ...]   # (path, line, text)
    detail: str                       # short human reason; '' when none
    unscreened: tuple[tuple[str, str], ...] = ()   # (name, why) the screen could not read


def _child_env(token: str, repo: str) -> dict[str, str]:
    """The child's environment, allowlisted: what the screen needs, nothing ambient."""
    env = {'GH_TOKEN': token, 'GH_REPO': repo}
    env.update({name: os.environ[name] for name in ENV_NAMES if name in os.environ})
    return env


def _spawn(argv: list, timeout: int, env: dict) -> tuple:
    """The default runner: the screen in its own session, killed whole on timeout."""
    child = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, start_new_session=True)
    try:
        out, err = child.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        child.communicate()
        raise
    return child.returncode, out, err


def _stderr_tail(text: str) -> str:
    """The child's stderr, bounded for a human reader."""
    return text.strip()[-500:]


UNSCREENED = re.compile(r'^could not screen (.+?): (.*)$')


def run_screen(repo: str, pr: int, *, token: str, runner=None, timeout_s: int = 240) -> ScreenOutcome:
    """Screen `repo`'s pull request `pr` and reduce the child's exit to the outcome.

    Exit 0 is clean; exit 1 is candidates (the child reaches it only when it
    screened every file); exit 2, a signal, a timeout or any other non-zero
    exit is could-not-screen with the rows it found kept and each stderr line
    matching `could not screen <name>: <why>` parsed into `unscreened`.
    """
    argv = [sys.executable, '-I', str(REPO_ROOT / 'check_pin_tests.py'), '--repo', repo, '--pr', str(pr)]
    try:
        code, out, err = runner(argv, timeout_s) if runner is not None else _spawn(argv, timeout_s, _child_env(token, repo))
    except subprocess.TimeoutExpired:
        return ScreenOutcome('could-not-screen', (), f'the screen exceeded its {timeout_s}s budget and was killed')
    if code == 0:
        return ScreenOutcome('clean', (), '')
    candidates = tuple((m.group(1), int(m.group(2)), m.group(3))
                       for m in (ROW.match(line) for line in out.splitlines()) if m)
    unscreened = tuple((m.group(1), m.group(2))
                       for m in (UNSCREENED.match(line) for line in err.splitlines()) if m)
    if code == 1:
        if candidates:
            return ScreenOutcome('candidates', candidates, '')
        return ScreenOutcome('could-not-screen', (), 'exit 1 with no candidate rows')
    if code < 0:
        try:
            name = signal.Signals(-code).name
        except ValueError:
            name = ''
        detail = f'the screen died on signal {-code}' + (f' ({name})' if name else '')
        return ScreenOutcome('could-not-screen', candidates, detail, unscreened)
    return ScreenOutcome('could-not-screen', candidates, _stderr_tail(err), unscreened)
