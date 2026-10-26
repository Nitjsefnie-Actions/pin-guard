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
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = _load('run', 'run.py')
screen_run = _load('screen_run', 'scripts', 'ci', 'screen_run.py')
review_submit = _load('review_submit', 'scripts', 'ci', 'review_submit.py')
status_comment = _load('status_comment', 'scripts', 'ci', 'status_comment.py')

REPO, PR, SHA, TOKEN = 'o/r', 9, 'a' * 40, 'tok-en'
LOGIN = 'token-owner'   # the run's token login
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


def user_call():
    """The `GET /user` read every run opens with."""
    return (('api', 'user'), None, json.dumps({'login': LOGIN}))


def pr_head_call(result=None):
    """The REST pull object the endpoint returns for that argv: `head.sha`,
    and no `headRefOid` — that field exists only in `gh pr view --json`,
    so a code reading it must fail this double, not refuse every run."""
    return (('api', f'repos/{REPO}/pulls/{PR}'),
            None, json.dumps({'head': {'sha': result or SHA}}))


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
EXPECTED_REVIEW = {'event': 'COMMENT', 'commit_id': SHA,
                   'body': f"{review_submit.MARKER}\n- {status_comment.inert('tests/off.py:3')}",
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
        gh = Gh(user_call(), pr_head_call(), pr_head_call(), status_find_call())
        code, _ = self.action(gh, Screen(screen_run.ScreenOutcome('clean', (), '')))
        self.assertEqual(code, 0)
        self.assertEqual([args for args, _ in gh.calls],
                         [user_call()[0], pr_head_call()[0], pr_head_call()[0], status_find_call()[0]])

    def test_an_unidentifiable_token_fails_before_any_screen_or_write(self):
        # GET /user is the run's first call: when it fails, nothing runs.
        error = subprocess.CalledProcessError(1, ['gh'], stderr='gh: HTTP 401: Bad credentials')
        gh = Gh((('api', 'user'), None, error))
        code, err = self.action(gh, Refuse())
        self.assertEqual(code, 1)
        self.assertIn('the token could not be identified', err)
        self.assertEqual([args for args, _ in gh.calls], [('api', 'user')])

    def test_the_screen_receives_the_pr_coordinates_and_the_token(self):
        gh = Gh(user_call(), pr_head_call(), pr_head_call(), status_find_call())
        screen = Screen(screen_run.ScreenOutcome('clean', (), ''))
        self.action(gh, screen)
        self.assertEqual(screen.calls, [(REPO, PR, TOKEN)])

    def test_candidates_with_fail_on_post_the_status_and_the_review_and_exit_1(self):
        gh = Gh(user_call(), pr_head_call(), pr_head_call(), files_call([diff_file('tests/on.py', *MIXED_PATCH)]),
                pr_comments_call(), pr_reviews_call(),
                review_post(payload(EXPECTED_REVIEW), REVIEW_URL),
                status_find_call(),
                status_post(status_body_check(f'Pin-test screen of head {SHA}: 2 candidate line(s).',
                                              '- ' + status_comment.inert('tests/on.py:2'), '- ' + status_comment.inert('tests/off.py:3'))))
        code, _ = self.action(gh, Screen(screen_run.ScreenOutcome('candidates', CANDIDATES, '')),
                              env={'PIN_GUARD_FAIL_ON_CANDIDATES': 'true'})
        self.assertEqual(code, 1)
        self.assertEqual([args for args, _ in gh.calls if '--method' in args],
                         [review_post(payload(EXPECTED_REVIEW), REVIEW_URL)[0],
                          status_post(status_body_check())[0]])

    def test_candidates_by_default_post_the_status_and_the_review_and_exit_0(self):
        gh = Gh(user_call(), pr_head_call(), pr_head_call(), files_call([diff_file('tests/on.py', *MIXED_PATCH)]),
                pr_comments_call(), pr_reviews_call(),
                review_post(payload(EXPECTED_REVIEW), REVIEW_URL),
                status_find_call(), status_post(status_body_check('- ' + status_comment.inert('tests/on.py:2'), '- ' + status_comment.inert('tests/off.py:3'))))
        code, _ = self.action(gh, Screen(screen_run.ScreenOutcome('candidates', CANDIDATES, '')))
        self.assertEqual(code, 0)
        self.assertEqual([args for args, _ in gh.calls if '--method' in args],
                         [review_post(payload(EXPECTED_REVIEW), REVIEW_URL)[0],
                          status_post(status_body_check())[0]])

    def test_a_could_not_screen_outcome_states_its_reason_and_exits_1(self):
        detail = 'the screen exceeded its 240s budget and was killed'
        gh = Gh(user_call(), pr_head_call(), pr_head_call(), status_find_call(),
                status_post(status_body_check('The screen could not run:', detail)))
        code, _ = self.action(gh, Screen(screen_run.ScreenOutcome('could-not-screen', (), detail)))
        self.assertEqual(code, 1)
        # The failed screen posts the status and nothing else.
        self.assertEqual([args for args, _ in gh.calls if '--method' in args],
                         [status_post(status_body_check())[0]])

    def test_a_could_not_screen_outcome_still_delivers_its_rows_and_fails(self):
        # A file defeated the screen after rows were found: the rows are still
        # delivered, the status comment names the unscreened files, and the
        # job fails either way.
        seam = Screen(screen_run.ScreenOutcome('could-not-screen', CANDIDATES, '',
                                               (('Dockerfile', 'HTTP 404: Not Found'),)))
        gh = Gh(user_call(), pr_head_call(), pr_head_call(), files_call([diff_file('tests/on.py', *MIXED_PATCH)]),
                pr_comments_call(), pr_reviews_call(),
                review_post(payload(EXPECTED_REVIEW), REVIEW_URL),
                status_find_call(),
                status_post(status_body_check('2 candidate line(s)',
                                              'could not be screened: Dockerfile: HTTP 404: Not Found')))
        code, _ = self.action(gh, seam)
        self.assertEqual(code, 1)
        self.assertEqual([args for args, _ in gh.calls if '--method' in args],
                         [review_post(payload(EXPECTED_REVIEW), REVIEW_URL)[0],
                          status_post(status_body_check())[0]])

    def test_a_truncated_status_comment_fails_even_when_fail_on_is_off(self):
        # One oversized candidate off the diff: the review carries it as a
        # body row, and the status comment cannot hold the full list.
        huge = ('tests/off.py', 3, 'x' * 70000)
        gh = Gh(user_call(), pr_head_call(), pr_head_call(), files_call(),
                pr_comments_call(), pr_reviews_call(),
                review_post(payload({'event': 'COMMENT', 'commit_id': SHA,
                                     'body': f"{review_submit.MARKER}\n- {status_comment.inert('tests/off.py:3')}",
                                     'comments': []}), REVIEW_URL),
                status_find_call(), status_post(status_body_check('1 candidate line(s)')))
        code, _ = self.action(gh, Screen(screen_run.ScreenOutcome('candidates', (huge,), '')))
        self.assertEqual(code, 1)

    def test_missing_env_fails_before_any_github_call_or_screen(self):
        gh, screen = Refuse(), Refuse()
        code, err = self.action(gh, screen, env={'PIN_GUARD_REPO': ''})
        self.assertEqual(code, 1)
        self.assertIn('PIN_GUARD_REPO', err)
        self.assertIn('PIN_GUARD_PR_NUMBER', err)
        code, err = self.action(gh, screen, env={'PIN_GUARD_PR_NUMBER': ''})
        self.assertEqual(code, 1)
        self.assertIn('PIN_GUARD_PR_NUMBER', err)

    def test_a_non_numeric_pr_number_fails_before_any_github_call_or_screen(self):
        gh, screen = Refuse(), Refuse()
        code, err = self.action(gh, screen, env={'PIN_GUARD_PR_NUMBER': 'nine'})
        self.assertEqual(code, 1)
        self.assertIn('PIN_GUARD_PR_NUMBER', err)
        self.assertIn('nine', err)

    def test_an_unknown_screen_status_fails_closed_through_the_status_comment(self):
        gh = Gh(user_call(), pr_head_call(), pr_head_call(), status_find_call(),
                status_post(status_body_check('the screen reported an unknown status')))
        code, _ = self.action(gh, Screen(screen_run.ScreenOutcome('mystery', (), '')))
        self.assertEqual(code, 1)
        self.assertEqual([args for args, _ in gh.calls if '--method' in args],
                         [status_post(status_body_check())[0]])

    def test_a_head_moving_during_the_screen_is_refused_unlabeled(self):
        # A push landing between the probe and the screen's own read must not
        # get a review or status labeled with the event SHA: the re-probe
        # after the screen refuses, and the rows it found are not delivered —
        # the fixture models no files listing and no review POST.
        gh = Gh(user_call(), pr_head_call(), pr_head_call('b' * 40), status_find_call(),
                status_post(status_body_check('the pull request head moved while the screen ran')))
        screen = Screen(screen_run.ScreenOutcome('candidates', CANDIDATES, ''))
        code, _ = self.action(gh, screen)
        self.assertEqual(code, 1)
        self.assertEqual([args for args, _ in gh.calls if '--method' in args],
                         [status_post(status_body_check())[0]])

    def test_a_moved_head_refuses_to_screen_and_fails(self):
        # The screen reads the PR at its CURRENT head while the event's SHA
        # labels the review and the status: when they differ, nothing is
        # screened, nothing is delivered, and the status comment says the
        # head moved. The screen double must never be called.
        gh = Gh(user_call(), pr_head_call('b' * 40), status_find_call(),
                status_post(status_body_check('the pull request head moved since the event')))
        screen = Screen(screen_run.ScreenOutcome('clean', (), ''))
        code, _ = self.action(gh, screen)
        self.assertEqual(code, 1)
        self.assertEqual(screen.calls, [])
        self.assertEqual([args for args, _ in gh.calls if '--method' in args],
                         [status_post(status_body_check())[0]])

    def test_hostile_paths_render_inert_in_the_review_body(self):
        # A filename holding an @mention and backticks lands in the review
        # body inside a code span its own backtick run cannot close.
        hostile = ('tests/@user`x`.py', 1, 'x == "1"')
        body = f'{review_submit.MARKER}\n- {status_comment.inert("tests/@user`x`.py:1")}'
        gh = Gh(user_call(), pr_head_call(), pr_head_call(), files_call(), pr_comments_call(), pr_reviews_call(),
                review_post(payload({'event': 'COMMENT', 'commit_id': SHA, 'body': body,
                                     'comments': []}), REVIEW_URL),
                status_find_call(), status_post(status_body_check('1 candidate line(s)')))
        code, _ = self.action(gh, Screen(screen_run.ScreenOutcome('candidates', (hostile,), '')))
        self.assertEqual(code, 0)

    def test_a_backtick_led_path_stays_inert_in_the_review_body(self):
        # A path starting with a backtick would merge with the span's opening
        # fence into a run nothing closes; inert pads one space each side so
        # CommonMark strips them and the row renders as <code>, no mention.
        hostile = ('` @octocat x/tests/test_a.py', 1, 'x == "1"')
        body = f'{review_submit.MARKER}\n- {status_comment.inert("` @octocat x/tests/test_a.py:1")}'
        gh = Gh(user_call(), pr_head_call(), pr_head_call(), files_call(), pr_comments_call(), pr_reviews_call(),
                review_post(payload({'event': 'COMMENT', 'commit_id': SHA, 'body': body,
                                     'comments': []}), REVIEW_URL),
                status_find_call(), status_post(status_body_check('1 candidate line(s)')))
        code, _ = self.action(gh, Screen(screen_run.ScreenOutcome('candidates', (hostile,), '')))
        self.assertEqual(code, 0)

    def test_a_prior_status_comment_is_edited_in_place_not_posted_again(self):
        earlier_body = status_comment.status_body('b' * 40, [('tests/old.py', 1, 'old == "1"')])[0]
        earlier = {'id': 7, 'user': {'login': LOGIN, 'type': 'User'},
                   'body': earlier_body}
        patch_args = ('api', f'repos/{REPO}/issues/{PR}/comments/7', '--method', 'PATCH', '--input', '-')
        gh = Gh(user_call(), pr_head_call(), pr_head_call(), status_find_call([earlier]),
                (patch_args,
                 status_body_check(f'Pin-test screen of head {SHA}', 'This head is clean'),
                 json.dumps({'html_url': 'https://example.com/s/7'})))
        code, _ = self.action(gh, Screen(screen_run.ScreenOutcome('clean', (), '')))
        self.assertEqual(code, 0)
        # The one write is the PATCH at the earlier comment's id, carrying the
        # new head's body; a POST would be an unmodeled call the double refuses.
        self.assertEqual([args for args, _ in gh.calls if '--method' in args], [patch_args])

    def test_a_delivery_exception_still_reports_the_screen_outcome_and_exits_1(self):
        error = subprocess.CalledProcessError(1, ['gh'], stderr='gh: GitHub surface refused the write')
        gh = Gh(user_call(), pr_head_call(), pr_head_call(), files_call([diff_file('tests/on.py', *MIXED_PATCH)]),
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

    def test_the_posted_review_is_recognized_as_the_action_s_own_at_the_next_screening(self):
        # The spanning control: what the first delivery puts on the wire is
        # what current_bot_comments must read back. The head is screened
        # again with the same candidates and the listings hold exactly the
        # first delivery's review and comment — so nothing new may be
        # planned, and the double models no review POST: one would refuse
        # the run. (A genuinely NEW head re-comments by design: the earlier
        # comment is outdated there — review_submit's own rule.)
        files = [diff_file('tests/on.py', *MIXED_PATCH),
                 diff_file('tests/off.py', '@@ -0,0 +1,4 @@', '+a', '+b', '+y == "2"', '+d')]
        both_inline = {'event': 'COMMENT', 'commit_id': SHA, 'body': review_submit.MARKER,
                       'comments': [{'path': path, 'line': line, 'side': 'RIGHT',
                                     'body': review_submit.render_candidate(text)}
                                    for path, line, text in CANDIDATES]}
        gh = Gh(user_call(), pr_head_call(), pr_head_call(), files_call(files), pr_comments_call(), pr_reviews_call(),
                review_post(payload(both_inline), REVIEW_URL),
                status_find_call(), status_post(status_body_check()))
        code, _ = self.action(gh, Screen(screen_run.ScreenOutcome('candidates', CANDIDATES, '')))
        self.assertEqual(code, 0)
        review_stdin = next(stdin for args, stdin in gh.calls
                            if '--input' in args and args[1] == f'repos/{REPO}/pulls/{PR}/reviews')
        posted = json.loads(review_stdin)
        # The marker rides the body's first line: the recognition rule
        # current_bot_comments applies to the parent review. Every candidate
        # anchored inline, so the body is the marker alone.
        self.assertEqual(posted['body'].splitlines()[0], review_submit.MARKER)
        self.assertEqual(posted['body'], review_submit.MARKER)

        reviews_page = [{'id': 10, 'user': {'login': LOGIN, 'type': 'User'},
                         'commit_id': SHA, 'body': posted['body']}]
        comments_page = [{'id': 11, 'pull_request_review_id': 10,
                          'user': {'login': LOGIN, 'type': 'User'},
                          'path': inline['path'], 'line': inline['line'], 'commit_id': SHA,
                          'body': inline['body']} for inline in posted['comments']]
        earlier_status = {'id': 7, 'user': {'login': LOGIN, 'type': 'User'},
                          'body': status_comment.status_body(SHA, [list(row) for row in CANDIDATES])[0]}
        patch_args = ('api', f'repos/{REPO}/issues/{PR}/comments/7', '--method', 'PATCH', '--input', '-')
        gh2 = Gh(user_call(), pr_head_call(), pr_head_call(), files_call(files),
                 pr_comments_call(comments_page), pr_reviews_call(reviews_page),
                 status_find_call([earlier_status]),
                 (patch_args, status_body_check(f'Pin-test screen of head {SHA}'),
                  json.dumps({'html_url': 'https://example.com/s/7'})))
        code, _ = self.action(gh2, Screen(screen_run.ScreenOutcome('candidates', CANDIDATES, '')))
        self.assertEqual(code, 0)
        # The status is edited in place and nothing else is written: the
        # existing inline comment absorbed its candidate.
        self.assertEqual([args for args, _ in gh2.calls if '--method' in args], [patch_args])


class DiffableLines(unittest.TestCase):
    """diffable_lines: the added-line anchors read from the files listing's patches."""

    def counted(self, *pages):
        gh = Gh(user_call(), pr_head_call(), pr_head_call(), files_call(*pages))
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
