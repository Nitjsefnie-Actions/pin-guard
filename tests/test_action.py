"""run.py: the action's orchestration over its three seams — the screen, gh, and nothing else.

Every GitHub surface run.py touches goes through its own `gh` callable and
the injected `run_screen` double; both refuse what they do not model, so a
clean head's run cannot hide a write and a mis-wired screen cannot pass.
"""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import unittest
from unittest import mock

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, *path):
    spec = importlib.util.spec_from_file_location(name, os.path.join(KIT, *path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = _load('run', 'run.py')
screen_run = _load('screen_run', 'scripts', 'ci', 'screen_run.py')
review_submit = _load('review_submit', 'scripts', 'ci', 'review_submit.py')
status_comment = _load('status_comment', 'scripts', 'ci', 'status_comment.py')

REPO, PR, SHA, TOKEN = 'o/r', 9, 'a' * 40, 'tok-en'
ENV = {'PIN_GUARD_REPO': REPO, 'PIN_GUARD_PR_NUMBER': str(PR), 'PIN_GUARD_HEAD_SHA': SHA,
       'PIN_GUARD_GITHUB_TOKEN': TOKEN}


class Gh:
    """A gh double: serves exactly the modeled calls, refuses every other.

    Each model is (args, payload, result): args must equal the call's argv
    exactly, payload is a predicate the call's stdin payload must satisfy
    (`None` models a read with no payload), and result is the stdout string
    or an exception to raise. A call matching no unconsumed model raises
    AssertionError, so an unmodeled endpoint, method or payload cannot pass
    silently — in particular the clean-head control models no write at all,
    so any POST or PATCH attempt refuses the run.
    """

    def __init__(self, *models):
        self.models = [[tuple(args), payload_check, result, False] for args, payload_check, result in models]
        self.calls = []

    def __call__(self, *args, stdin=None):
        reasons = []
        for model in self.models:
            if model[3] or args != model[0]:
                continue
            try:
                if model[1] is None and stdin is not None:
                    raise AssertionError(f'a read must carry no payload, got {stdin[:80]!r}')
                if callable(model[1]):
                    model[1](stdin)
                model[3] = True
                self.calls.append((args, stdin))
                if isinstance(model[2], Exception):
                    raise model[2]
                return model[2]
            except AssertionError as error:
                reasons.append(f'{args[:2]}: {error}')
        raise AssertionError('unmodeled gh call %r stdin=%r\n  %s'
                             % (args, str(stdin)[:120], '\n  '.join(reasons) or 'no model named this argv'))


class Screen:
    """A run_screen double modeling one outcome; a second call is refused."""

    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def __call__(self, repo, pr, *, token=None):
        if self.calls:
            raise AssertionError(f'unmodeled second screen run {repo} {pr}')
        self.calls.append((repo, pr, token))
        return self.outcome


class Refuse:
    """A stand-in that must never be called: any call fails the test."""

    def __call__(self, *args, **kwargs):
        raise AssertionError(f'unmodeled call {args} {kwargs}')


def payload(check):
    """A predicate asserting the call's payload parses to exactly `check`."""
    def predicate(stdin):
        assert stdin is not None, 'the write must carry its JSON payload on stdin'
        assert json.loads(stdin) == check, json.dumps(json.loads(stdin), indent=1)[:400]
        return True
    return predicate


def status_body_check(*must):
    """A predicate asserting the status comment's rendered body carries every named text."""
    def predicate(stdin):
        assert stdin is not None, 'the write must carry its JSON payload on stdin'
        body = json.loads(stdin)['body']
        for text in must:
            assert text in body, f'{text!r} not in the status body'
        return True
    return predicate


def json_slurp(*pages):
    """The stdout `gh api --paginate --slurp` prints: one array of page arrays."""
    return json.dumps(list(pages))


def files_call(*pages):
    return (('api', f'repos/{REPO}/pulls/{PR}/files?per_page=100', '--paginate', '--slurp'),
            None, json_slurp(*pages))


def pr_comments_call(*pages):
    return (('api', f'repos/{REPO}/pulls/{PR}/comments', '--paginate', '--slurp'),
            None, json_slurp(*pages))


def pr_reviews_call(*pages):
    return (('api', f'repos/{REPO}/pulls/{PR}/reviews', '--paginate', '--slurp'),
            None, json_slurp(*pages))


def status_find_call(*pages):
    return (('api', f'repos/{REPO}/issues/{PR}/comments', '--paginate', '--slurp'),
            None, json_slurp(*pages))


def review_post(check, result):
    return (('api', f'repos/{REPO}/pulls/{PR}/reviews', '--method', 'POST', '--input', '-'),
            check, result)


def status_post(check):
    return (('api', f'repos/{REPO}/issues/{PR}/comments', '--method', 'POST', '--input', '-'),
            check, json.dumps({'html_url': 'https://example.com/s/1'}))


def diff_file(path, *patch_lines):
    """A files-listing page item carrying a patch verbatim."""
    return {'filename': path, 'patch': '\n'.join(patch_lines)}


# One candidate on the diff (tests/on.py:2), one off it (tests/off.py:3).
MIXED_PATCH = ['@@ -1,3 +1,4 @@', ' ctx', '-deleted', '+added here', '+added too', ' ctx2']
ON_CANDIDATE = ('tests/on.py', 2, 'x == "1"')
OFF_CANDIDATE = ('tests/off.py', 3, 'y == "2"')
CANDIDATES = (ON_CANDIDATE, OFF_CANDIDATE)
EXPECTED_REVIEW = {'event': 'COMMENT', 'commit_id': SHA, 'body': '- tests/off.py:3',
                   'comments': [{'path': 'tests/on.py', 'line': 2, 'side': 'RIGHT',
                                 'body': review_submit.render_candidate('x == "1"')}]}
REVIEW_URL = json.dumps({'html_url': 'https://example.com/r/1'})


class Action(unittest.TestCase):
    """main() over the injected screen and gh seams: the exit contract row by row."""

    def action(self, gh, screen, env=None):
        environment = dict(ENV)
        environment.update(env or {})
        with mock.patch.dict(os.environ, environment, clear=True), \
                mock.patch.object(mod, 'gh', gh), \
                mock.patch.object(mod, 'run_screen', screen):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = mod.main()
        return code, err.getvalue()

    def test_a_clean_head_with_no_prior_status_writes_nothing_and_exits_0(self):
        # Only the status read is modeled: any POST or PATCH is an unmodeled
        # call the double refuses, so this control proves the zero-write path.
        gh = Gh(status_find_call())
        code, _ = self.action(gh, Screen(screen_run.ScreenOutcome('clean', (), '')))
        self.assertEqual(code, 0)
        self.assertEqual([args for args, _ in gh.calls], [status_find_call()[0]])

    def test_the_screen_receives_the_pr_coordinates_and_the_token(self):
        gh = Gh(status_find_call())
        screen = Screen(screen_run.ScreenOutcome('clean', (), ''))
        self.action(gh, screen)
        self.assertEqual(screen.calls, [(REPO, PR, TOKEN)])

    def test_candidates_with_fail_on_post_the_status_and_the_review_and_exit_1(self):
        gh = Gh(files_call([diff_file('tests/on.py', *MIXED_PATCH)]),
                pr_comments_call(), pr_reviews_call(),
                review_post(payload(EXPECTED_REVIEW), REVIEW_URL),
                status_find_call(),
                status_post(status_body_check(f'Pin-test screen of head {SHA}: 2 candidate line(s).',
                                              '- tests/on.py:2', '- tests/off.py:3')))
        code, _ = self.action(gh, Screen(screen_run.ScreenOutcome('candidates', CANDIDATES, '')),
                              env={'PIN_GUARD_FAIL_ON_CANDIDATES': 'true'})
        self.assertEqual(code, 1)
        self.assertEqual([args for args, _ in gh.calls if '--method' in args],
                         [review_post(payload(EXPECTED_REVIEW), REVIEW_URL)[0],
                          status_post(status_body_check())[0]])

    def test_candidates_by_default_post_the_status_and_the_review_and_exit_0(self):
        gh = Gh(files_call([diff_file('tests/on.py', *MIXED_PATCH)]),
                pr_comments_call(), pr_reviews_call(),
                review_post(payload(EXPECTED_REVIEW), REVIEW_URL),
                status_find_call(), status_post(status_body_check('- tests/on.py:2', '- tests/off.py:3')))
        code, _ = self.action(gh, Screen(screen_run.ScreenOutcome('candidates', CANDIDATES, '')))
        self.assertEqual(code, 0)
        self.assertEqual([args for args, _ in gh.calls if '--method' in args],
                         [review_post(payload(EXPECTED_REVIEW), REVIEW_URL)[0],
                          status_post(status_body_check())[0]])

    def test_a_could_not_screen_outcome_states_its_reason_and_exits_1(self):
        detail = 'the screen exceeded its 240s budget and was killed'
        gh = Gh(status_find_call(),
                status_post(status_body_check(f'The screen could not run: {detail}')))
        code, _ = self.action(gh, Screen(screen_run.ScreenOutcome('could-not-screen', (), detail)))
        self.assertEqual(code, 1)
        # The failed screen posts the status and nothing else.
        self.assertEqual([args for args, _ in gh.calls if '--method' in args],
                         [status_post(status_body_check())[0]])

    def test_a_truncated_status_comment_fails_even_when_fail_on_is_off(self):
        # One oversized candidate off the diff: the review carries it as a
        # body row, and the status comment cannot hold the full list.
        huge = ('tests/off.py', 3, 'x' * 70000)
        gh = Gh(files_call(),
                pr_comments_call(), pr_reviews_call(),
                review_post(payload({'event': 'COMMENT', 'commit_id': SHA,
                                     'body': '- tests/off.py:3', 'comments': []}), REVIEW_URL),
                status_find_call(), status_post(status_body_check('1 candidate line(s)')))
        code, _ = self.action(gh, Screen(screen_run.ScreenOutcome('candidates', (huge,), '')))
        self.assertEqual(code, 1)

    def test_missing_env_fails_before_any_github_call_or_screen(self):
        gh, screen = Refuse(), Refuse()
        code, err = self.action(gh, screen, env={'PIN_GUARD_REPO': ''})
        self.assertEqual(code, 1)
        self.assertIn('PIN_GUARD_REPO', err)
        self.assertIn('PIN_GUARD_PR_NUMBER', err)
        for missing in ('PIN_GUARD_PR_NUMBER',):
            code, err = self.action(gh, screen, env={missing: ''})
            self.assertEqual(code, 1)
            self.assertIn(missing, err)

    def test_a_delivery_exception_still_reports_the_screen_outcome_and_exits_1(self):
        error = subprocess.CalledProcessError(1, ['gh'], stderr='gh: GitHub surface refused the write')
        gh = Gh(files_call([diff_file('tests/on.py', *MIXED_PATCH)]),
                pr_comments_call(), pr_reviews_call(),
                review_post(payload(EXPECTED_REVIEW), error),
                status_find_call(),
                status_post(status_body_check('2 candidate line(s)',
                                              'the review could not be posted',
                                              'gh: GitHub surface refused the write')))
        code, _ = self.action(gh, Screen(screen_run.ScreenOutcome('candidates', CANDIDATES, '')))
        self.assertEqual(code, 1)
        # The status body's visible part carries the count and the stated
        # delivery failure; the hidden block carries the full candidate list.
        body = json.loads(gh.calls[-1][1])['body']
        self.assertEqual(status_comment.parse_block(body)['candidates'],
                         [list(row) for row in CANDIDATES])
        self.assertEqual([args for args, _ in gh.calls if '--method' in args],
                         [review_post(payload(EXPECTED_REVIEW), error)[0],
                          status_post(status_body_check())[0]])


class DiffableLines(unittest.TestCase):
    """diffable_lines: the added-line anchors read from the files listing's patches."""

    def counted(self, *pages):
        gh = Gh(files_call(*pages))
        with mock.patch.object(mod, 'gh', gh):
            return gh, mod.diffable_lines(REPO, PR)

    def test_added_lines_are_counted_from_each_hunk_s_new_start(self):
        patch = ['@@ -1,3 +1,4 @@', ' ctx', '-deleted', '+added here', '+added too', ' ctx2',
                 '@@ -10 +12,2 @@', '+second hunk', ' ctx']
        _, got = self.counted([diff_file('tests/on.py', *patch)])
        self.assertEqual(got, {('tests/on.py', 2), ('tests/on.py', 3), ('tests/on.py', 12)})

    def test_a_file_without_a_patch_yields_no_anchors(self):
        _, got = self.counted([{'filename': 'big.lock'}])
        self.assertEqual(got, set())


if __name__ == '__main__':
    unittest.main()
