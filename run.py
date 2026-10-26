#!/usr/bin/env python3
"""The action's runner: screen the pull request's head, deliver its findings, post the status.

Read entirely from the environment the action's run step passes (inputs are
data: none becomes shell program text), runs the repository's pin-test
screen at the PR head through `screen_run`, and delivers the outcome:

  - The sticky status comment is always upserted; it names the screened
    head, the candidate count and each candidate as `path:line`, and it
    states why the job failed whenever it does.
  - Candidates whose anchor line is on the PR diff become review line
    comments, the rest are listed in the review body; the review carries
    only candidates not already commented by the action at this head.
    The action never resolves or dismisses a thread itself.

The action's identity is the login its configured token posts as.

Exit code 0: a completed screen was delivered — clean, or candidates with
`PIN_GUARD_FAIL_ON_CANDIDATES` off. Exit code 1: candidates with it on, a
screen that could not run, a truncated status comment, or any
GitHub-surface failure — never the bare presence of candidates.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'scripts' / 'ci'))

from review_submit import MARKER, current_bot_comments, plan_comments, submit_review
from screen_run import ScreenOutcome, run_screen
from status_comment import inert, upsert_status

_HUNK = re.compile(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@')


def gh(*args, stdin=None):
    """The GitHub seam: one `gh` invocation; a write's JSON payload rides on stdin."""
    argv = ['gh', *args]
    result = subprocess.run(argv, input=stdin, text=True, capture_output=True)
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, argv,
                                            output=result.stdout, stderr=result.stderr)
    return result.stdout


def diffable_lines(repo, pr):
    """The `(path, added line)` pairs an inline comment can anchor on.

    Reads `repos/{repo}/pulls/{pr}/files?per_page=100` with `--paginate
    --slurp` (one array of page arrays, flattened here) and counts each
    patch's added lines from its hunk's new start; a file without a patch
    (binary, too large) yields no anchors.
    """
    pages = json.loads(gh('api', f'repos/{repo}/pulls/{pr}/files?per_page=100', '--paginate', '--slurp'))
    diffable = set()
    for item in (file for page in pages for file in page):
        patch = item.get('patch')
        if not patch:
            continue
        line = 0
        for text in patch.splitlines():
            header = _HUNK.match(text)
            if header:
                line = int(header.group(1))
            elif text.startswith('+'):
                diffable.add((item['filename'], line))
                line += 1
            elif text.startswith('-') or text.startswith('\\'):
                continue
            else:
                line += 1
    return diffable


def deliver(repo, pr, head_sha, candidates, bot_login):
    """Comment the head's candidates: inline on the diff, the rest in the review body.

    The review body carries MARKER on its own line: current_bot_comments
    recognises the action's own earlier comments only under a parent review
    whose body does, so a markerless body would re-comment every candidate
    on every later head.
    """
    existing = current_bot_comments(gh, repo, pr, head_sha, bot_login)
    inline, body_only = plan_comments(candidates, existing, diffable_lines(repo, pr))
    if not inline and not body_only:
        return
    body = '\n'.join([MARKER, *(f'- {inert(f"{path}:{line}")}' for path, line, _ in body_only)])
    submit_review(gh, repo, pr, head_sha, body, inline, bot_login)


def main():
    repo = os.environ.get('PIN_GUARD_REPO', '')
    number = os.environ.get('PIN_GUARD_PR_NUMBER', '')
    if not repo or not number:
        print('pin-guard: PIN_GUARD_REPO and PIN_GUARD_PR_NUMBER are both required '
              '(run this action on a pull request event); nothing was screened, '
              'no GitHub call was made.', file=sys.stderr)
        return 1
    try:
        pr = int(number)
    except ValueError:
        print(f'pin-guard: PIN_GUARD_PR_NUMBER must be a number, got {number!r}.', file=sys.stderr)
        return 1
    token = os.environ.get('PIN_GUARD_GITHUB_TOKEN', '')
    head_sha = os.environ.get('PIN_GUARD_HEAD_SHA', '')
    fail_on = os.environ.get('PIN_GUARD_FAIL_ON_CANDIDATES', '') == 'true'

    # The login the token posts as.
    try:
        login = json.loads(gh('api', 'user'))['login']
    except Exception as probe:
        print(f'pin-guard: the token could not be identified ({probe}); no screen ran '
              'and no comment was read or posted.', file=sys.stderr)
        return 1

    # The screen reads the PR at its CURRENT head through the API, while the
    # event's SHA labels the review and the status: screen only when they are
    # still the same commit, refuse a moved head outright, and re-probe after
    # the screen so a push that lands mid-screen is never labeled with the
    # event's SHA. The pull object's field is `head.sha` (`headRefOid` is the
    # `gh pr view --json` spelling and is absent from the REST response).
    try:
        current = json.loads(gh('api', f'repos/{repo}/pulls/{pr}'))['head']['sha']
    except Exception as probe:
        outcome = ScreenOutcome('could-not-screen', (), f'the pull request head could not be read: {probe}')
    else:
        if current != head_sha:
            outcome = ScreenOutcome(
                'could-not-screen', (),
                f'the pull request head moved since the event: the event head was {head_sha}, '
                f'the pull request head is now {current}')
        else:
            outcome = run_screen(repo, pr, token=token)
            try:
                rescreened = json.loads(gh('api', f'repos/{repo}/pulls/{pr}'))['head']['sha']
            except Exception as probe:
                outcome = ScreenOutcome('could-not-screen', (),
                                        f'the pull request head could not be read after the screen: {probe}')
            else:
                if rescreened != current:
                    outcome = ScreenOutcome(
                        'could-not-screen', (),
                        f'the pull request head moved while the screen ran: it was screened at {current}, '
                        f'the pull request head is now {rescreened}')

    candidates, error = (), ''
    if outcome.status == 'clean':
        pass
    elif outcome.status in ('candidates', 'could-not-screen'):
        candidates = outcome.candidates
        if candidates:
            try:
                deliver(repo, pr, head_sha, candidates, login)
            except Exception as delivery:  # stated in the status comment, never silent
                reason = str(delivery)
                stderr = getattr(delivery, 'stderr', None)
                if stderr:
                    reason += f': {stderr.strip()}'
                error = f'the review could not be posted: {reason}'
        if outcome.status == 'could-not-screen':
            screen_failure = (f'{len(outcome.unscreened)} file(s) could not be screened: '
                              + ', '.join(f'{name}: {why}' for name, why in outcome.unscreened)
                              ) if outcome.unscreened else outcome.detail
            error = f'{screen_failure}; {error}' if error else screen_failure
    else:
        # Fail closed: an unknown status must never read as a clean screen.
        error = f'the screen reported an unknown status {outcome.status!r}'

    _, _, truncated = upsert_status(gh, repo, pr, head_sha, candidates, bot_login=login,
                                    clean=outcome.status == 'clean', error=error or None,
                                    outcome=outcome.status)
    if error or outcome.status == 'could-not-screen' or truncated:
        return 1
    if candidates and fail_on:
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
