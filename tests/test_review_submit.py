"""review_submit: the action's own review line comments found, planned and posted."""
import importlib.util
import json
import os
import subprocess
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location('review_submit', os.path.join(KIT, 'scripts', 'ci', 'review_submit.py'))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

REPO, PR = 'o/r', 9
SHA = 'a' * 40
BOT = 'github-actions[bot]'
BODY = f'Screened by the pin-test screen.\n\n{mod.MARKER}'


class Gh:
    """A gh double: serves exactly the modeled calls, refuses every other.

    Each model is (args, payload, result): args must equal the call's argv
    exactly, payload is a predicate the call's stdin payload must satisfy
    (`None` models a read with no payload), and result is the stdout string
    or an exception to raise. A call matching no unconsumed model raises
    AssertionError, so an unmodeled endpoint, method or payload cannot pass
    silently.
    """

    def __init__(self, *models):
        self.models = [[tuple(args), payload, result, False] for args, payload, result in models]
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


def payload(check):
    """A predicate asserting the call's payload parses to exactly `check`."""
    def predicate(stdin):
        assert stdin is not None, 'the write must carry its JSON payload on stdin'
        assert json.loads(stdin) == check, json.dumps(json.loads(stdin), indent=1)[:400]
        return True
    return predicate


def review(rid, body, login=BOT, user_type='Bot'):
    """A review page item."""
    return {'id': rid, 'user': {'login': login, 'type': user_type}, 'body': body}


def comment(cid, rid, path, line, body, *, commit_id=SHA, reply_to=None, login=BOT, user_type='Bot'):
    """A pull-request inline comment page item; reply_to models in_reply_to_id."""
    item = {'id': cid, 'pull_request_review_id': rid, 'path': path, 'line': line,
            'commit_id': commit_id, 'body': body, 'user': {'login': login, 'type': user_type}}
    if reply_to is not None:
        item['in_reply_to_id'] = reply_to
    return item


def json_slurp(*pages):
    """The stdout `gh api --paginate --slurp` prints: one array of page arrays."""
    return json.dumps(list(pages))


def comments_call(*pages):
    return ('api', f'repos/{REPO}/pulls/{PR}/comments', '--paginate', '--slurp'), None, json_slurp(*pages)


def reviews_call(*pages):
    return ('api', f'repos/{REPO}/pulls/{PR}/reviews', '--paginate', '--slurp'), None, json_slurp(*pages)


class CurrentBotComments(unittest.TestCase):
    """current_bot_comments: which inline comments are the action's own and current."""

    def current(self, comments, reviews):
        gh = Gh(comments_call(comments), reviews_call(reviews))
        got = mod.current_bot_comments(gh, REPO, PR, SHA, BOT)
        return gh, got

    def test_the_markers_are_the_pinned_spellings(self):
        self.assertEqual(mod.MARKER, '<!-- pin-guard: review -->')

    def test_the_action_s_own_current_comment_is_returned_with_its_quoted_text(self):
        text = 'assert upload["with"]["name"] == "ratchet-push"'
        gh, got = self.current(
            [comment(1, 10, 'tests/x.py', 7, mod.render_candidate(text))],
            [review(10, BODY)])
        self.assertEqual(got, [{**comment(1, 10, 'tests/x.py', 7, mod.render_candidate(text)),
                                'quoted_text': text}])
        # Both listings are read with --paginate, so a longer PR is read whole.
        for args, _ in gh.calls:
            self.assertIn('--paginate', args)

    def test_a_round_trip_through_a_rendered_candidate_keeps_the_text_exact(self):
        text = 'run `npm ci` #12 --> @reviewer if `x` == "1.0"'
        _, got = self.current(
            [comment(1, 10, 'tests/x.py', 7, mod.render_candidate(text))],
            [review(10, BODY)])
        self.assertEqual(got[0]['quoted_text'], text)

    def test_a_marker_mid_line_is_not_a_marker(self):
        _, got = self.current(
            [comment(1, 10, 'tests/x.py', 7, mod.render_candidate('x == "1"'))],
            [review(10, f'before {mod.MARKER} after')])
        self.assertEqual(got, [])

    def test_a_marker_inside_quoted_candidate_text_is_not_a_marker(self):
        # The review quotes a candidate whose text carries the marker: only an
        # own-line marker in the review's own body recognizes the review.
        quoted = mod.render_candidate(f'x == "1"  # {mod.MARKER}')
        _, got = self.current(
            [comment(1, 10, 'tests/x.py', 7, quoted)],
            [review(10, f'Findings:\n{quoted}')])
        self.assertEqual(got, [])

    def test_a_non_bot_author_is_not_the_action(self):
        _, got = self.current(
            [comment(1, 10, 'tests/x.py', 7, mod.render_candidate('x == "1"')),
             comment(2, 10, 'tests/x.py', 8, mod.render_candidate('y == "2"'), login=BOT, user_type='User'),
             comment(3, 11, 'tests/x.py', 9, mod.render_candidate('z == "3"'))],
            [review(10, BODY, login=BOT, user_type='User'), review(11, BODY, login='someone-else')])
        self.assertEqual(got, [])

    def test_the_wrong_marker_does_not_recognize_a_review(self):
        _, got = self.current(
            [comment(1, 10, 'tests/x.py', 7, mod.render_candidate('x == "1"'))],
            [review(10, 'Status comment marker only.\n\n<!-- pin-guard: status -->')])
        self.assertEqual(got, [])

    def test_a_comment_on_another_head_is_not_current(self):
        _, got = self.current(
            [comment(1, 10, 'tests/x.py', 7, mod.render_candidate('x == "1"'), commit_id='b' * 40)],
            [review(10, BODY)])
        self.assertEqual(got, [])

    def test_a_reply_is_not_current(self):
        _, got = self.current(
            [comment(1, 10, 'tests/x.py', 7, mod.render_candidate('x == "1"'), reply_to=1)],
            [review(10, BODY)])
        self.assertEqual(got, [])

    def test_a_comment_without_a_readable_quoted_field_is_skipped(self):
        _, got = self.current(
            [comment(1, 10, 'tests/x.py', 7, 'a comment by a squatted marker, no field')],
            [review(10, BODY)])
        self.assertEqual(got, [])

    def test_a_listing_split_across_two_pages_reads_whole_and_dedupes(self):
        # 31 inline comments: GitHub's default page of 30 plus one on the
        # next page. `--paginate --slurp` prints one array of page arrays,
        # the read flattens it, and the last page's comment dedupes its
        # candidate like any other.
        first_page = [comment(n, 10, f'tests/p{n}.py', n + 1, mod.render_candidate(f'x{n} == "1"'))
                      for n in range(30)]
        last = comment(30, 10, 'tests/x.py', 7, mod.render_candidate('the thirty first'))
        gh = Gh(comments_call(first_page, [last]), reviews_call([review(10, BODY)]))
        got = mod.current_bot_comments(gh, REPO, PR, SHA, BOT)
        self.assertEqual(len(got), 31)
        self.assertEqual(got[30]['quoted_text'], 'the thirty first')
        candidates = ([(f'tests/p{n}.py', n + 1, f'x{n} == "1"') for n in range(30)]
                      + [('tests/x.py', 7, 'the thirty first')])
        got_plan = mod.plan_comments(candidates, got, {(p, n) for p, n, _ in candidates})
        self.assertEqual(got_plan, ([], []))


class PlanComments(unittest.TestCase):
    """plan_comments: which candidates are new, and where each one routes."""

    def test_a_current_matching_comment_absorbs_its_candidate(self):
        existing = [{**comment(1, 10, 'tests/x.py', 7, mod.render_candidate('x == "1"')),
                     'quoted_text': 'x == "1"'}]
        got = mod.plan_comments([('tests/x.py', 7, 'x == "1"')], existing, {('tests/x.py', 7)})
        self.assertEqual(got, ([], []))

    def test_one_existing_comment_absorbs_exactly_one_occurrence(self):
        existing = [{**comment(1, 10, 'tests/x.py', 7, mod.render_candidate('x == "1"')),
                     'quoted_text': 'x == "1"'}]
        got = mod.plan_comments([('tests/x.py', 7, 'x == "1"'), ('tests/x.py', 7, 'x == "1"')],
                                existing, {('tests/x.py', 7)})
        self.assertEqual(got, ([('tests/x.py', 7, 'x == "1"')], []))

    def test_identical_text_on_a_different_path_is_not_a_match(self):
        existing = [{**comment(1, 10, 'tests/x.py', 7, mod.render_candidate('x == "1"')),
                     'quoted_text': 'x == "1"'}]
        got = mod.plan_comments([('tests/other.py', 7, 'x == "1"')], existing,
                                {('tests/other.py', 7), ('tests/x.py', 7)})
        self.assertEqual(got, ([('tests/other.py', 7, 'x == "1"')], []))

    def test_an_outdated_prior_leaves_its_candidate_new(self):
        # The prior comment anchors on an earlier head, so current_bot_comments
        # does not report it and the candidate is planned again.
        gh, got_current = CurrentBotComments('current').current(
            [comment(1, 10, 'tests/x.py', 7, mod.render_candidate('x == "1"'), commit_id='b' * 40)],
            [review(10, BODY)])
        self.assertEqual(got_current, [])
        got = mod.plan_comments([('tests/x.py', 7, 'x == "1"')], got_current, {('tests/x.py', 7)})
        self.assertEqual(got, ([('tests/x.py', 7, 'x == "1"')], []))

    def test_a_candidate_off_the_diff_routes_to_the_review_body(self):
        got = mod.plan_comments(
            [('tests/on.py', 7, 'x == "1"'), ('tests/off.py', 3, 'y == "2"')],
            [], {('tests/on.py', 7)})
        self.assertEqual(got, ([('tests/on.py', 7, 'x == "1"')],
                               [('tests/off.py', 3, 'y == "2"')]))


class RenderCandidate(unittest.TestCase):
    """render_candidate: hostile text rendered inert, plus its guidance."""

    def test_the_guidance_is_the_pinned_wording(self):
        self.assertIn('If this line pins a dependency, delete it, then resolve this thread. '
                      'If it is a consistency check between files or steps, or a security invariant enumerated '
                      'over every workflow, resolve the thread with a reply naming why.',
                      mod.render_candidate('x'))

    def test_the_quoted_field_has_a_known_answer(self):
        # The persisted format pinned against a hand-written expected line:
        # every quoted-field test otherwise renders and reads through the
        # same module, where a symmetric writer+reader mutant stays green.
        text = 'a `b` --> c'
        expected = '<!-- pin-guard: quoted {"text": "a `b` &dash;&dash;&gt; c"} -->'
        self.assertTrue(mod.render_candidate(text).endswith(expected))

    def test_a_backtick_run_widens_the_fence(self):
        self.assertTrue(mod.render_candidate('x ``````` y').startswith('````````\nx ``````` y\n````````'))

    def test_no_backticks_gets_a_three_tick_fence(self):
        self.assertTrue(mod.render_candidate('x == "1"').startswith('```\nx == "1"\n```'))

    def test_hostile_text_lands_inside_the_fence(self):
        text = 'ping @owner see #12 --> <script>alert("pin")</script> if v == "1.0"'
        rendered = mod.render_candidate(text)
        first, rest = rendered.split('\n', 1)
        fence = '`' * len(first)
        self.assertTrue(rest.startswith(f'{text}\n{fence}\n'))
        # The quoted-text field round-trips the hostile text exactly.
        _, got = CurrentBotComments('current').current(
            [comment(1, 10, 'tests/x.py', 7, rendered)],
            [review(10, BODY)])
        self.assertEqual(got[0]['quoted_text'], text)


class SubmitReview(unittest.TestCase):
    """submit_review: the review POST, its URL, and the 422 split."""

    def review_call(self, comments, result):
        args = ('api', f'repos/{REPO}/pulls/{PR}/reviews', '--method', 'POST', '--input', '-')
        return args, payload({'event': 'COMMENT', 'commit_id': SHA, 'body': BODY,
                              'comments': [{'path': p, 'line': l, 'side': 'RIGHT',
                                            'body': mod.render_candidate(t)} for p, l, t in comments]}), result

    def test_a_review_is_posted_and_its_url_returned(self):
        gh = Gh(self.review_call([('tests/x.py', 7, 'x == "1"')],
                                 json.dumps({'html_url': 'https://example.com/r/1'})))
        got = mod.submit_review(gh, REPO, PR, SHA, BODY, [('tests/x.py', 7, 'x == "1"')], BOT)
        self.assertEqual(got, ['https://example.com/r/1'])

    def test_a_body_only_review_still_posts(self):
        gh = Gh(self.review_call([], json.dumps({'html_url': 'https://example.com/r/2'})))
        got = mod.submit_review(gh, REPO, PR, SHA, BODY, [], BOT)
        self.assertEqual(got, ['https://example.com/r/2'])

    def test_a_422_splits_the_comments_across_fresh_reviews(self):
        # GitHub refuses the whole set as oversized; each half lands in its own
        # review and every candidate appears in exactly one accepted payload.
        error = subprocess.CalledProcessError(1, ['gh'], stderr='gh: HTTP 422: Unprocessable Entity')
        gh = Gh(self.review_call([('tests/x.py', 7, 'x == "1"'), ('tests/y.py', 3, 'y == "2"')], error),
                self.review_call([('tests/x.py', 7, 'x == "1"')], json.dumps({'html_url': 'https://example.com/r/3'})),
                self.review_call([('tests/y.py', 3, 'y == "2"')], json.dumps({'html_url': 'https://example.com/r/4'})))
        got = mod.submit_review(gh, REPO, PR, SHA, BODY,
                                [('tests/x.py', 7, 'x == "1"'), ('tests/y.py', 3, 'y == "2"')], BOT)
        self.assertEqual(got, ['https://example.com/r/3', 'https://example.com/r/4'])
        # gh.calls[0] is the refused POST; both halves landed after it.
        accepted = [c for _, stdin in gh.calls[1:] for c in json.loads(stdin)['comments']]
        self.assertEqual(sorted(c['path'] for c in accepted), ['tests/x.py', 'tests/y.py'])

    def test_a_422_on_a_single_comment_is_raised_not_hidden(self):
        error = subprocess.CalledProcessError(1, ['gh'], stderr='gh: HTTP 422: line too long')
        gh = Gh(self.review_call([('tests/x.py', 7, 'x == "1"')], error))
        with self.assertRaises(subprocess.CalledProcessError):
            mod.submit_review(gh, REPO, PR, SHA, BODY, [('tests/x.py', 7, 'x == "1"')], BOT)
        self.assertEqual(len(gh.calls), 1)


if __name__ == '__main__':
    unittest.main()
