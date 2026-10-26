"""status_comment: the sticky per-head status comment found, rendered and upserted."""
import importlib.util
import json
import os
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location('status_comment', os.path.join(KIT, 'scripts', 'ci', 'status_comment.py'))
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

REPO, PR = 'o/r', 9
SHA = 'a' * 40
BOT = 'github-actions[bot]'


class Gh:
    """A gh double: serves exactly the modeled calls, refuses every other.

    Each model is (args, payload, result): args must equal the call's argv
    exactly, payload is a predicate the call's stdin payload must satisfy
    (`None` models a read with no payload), and result is the stdout string
    or an exception to raise. A call matching no unconsumed model raises
    AssertionError, so an unmodeled endpoint, method or payload cannot pass
    silently — in particular a clean head with no earlier status comment
    has no modeled write at all, so any write attempt refuses the run.
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


def payload(check):
    """A predicate asserting the call's payload parses to exactly `check`."""
    def predicate(stdin):
        assert stdin is not None, 'the write must carry its JSON payload on stdin'
        assert json.loads(stdin) == check, json.dumps(json.loads(stdin), indent=1)[:400]
        return True
    return predicate


def comment(cid, body, login=BOT, user_type='Bot'):
    """An issue-comment page item."""
    return {'id': cid, 'user': {'login': login, 'type': user_type}, 'body': body}


def listing_call(*pages):
    """A `--paginate --slurp` listing: one call printing one array of page arrays."""
    return (('api', f'repos/{REPO}/issues/{PR}/comments', '--paginate', '--slurp'),
            None, json.dumps(list(pages)))


def block_of(body):
    """The hidden block's escaped JSON text, read the way a consumer reads it."""
    _, marker, after = body.rpartition(f'\n{mod.STATUS_MARKER}\n')
    assert marker, 'the rendered body must carry the status marker on its own line'
    _, sep, tail = after.partition('<!-- ')
    assert sep and tail.endswith(' -->'), f'no hidden block after the marker: {after!r}'
    return tail[:-4]


class FindStatus(unittest.TestCase):
    """find_status: the one sticky comment that is the action's own."""

    def test_the_marker_is_the_pinned_spelling(self):
        self.assertEqual(mod.STATUS_MARKER, '<!-- pin-guard: status -->')

    def found(self, body, login=BOT, user_type='Bot'):
        gh = Gh(listing_call([comment(7, 'an unrelated question'), comment(9, body, login, user_type)]))
        return gh, mod.find_status(gh, REPO, PR, BOT)

    def test_the_action_s_own_status_comment_is_found(self):
        gh, got = self.found('A screen report.\n\n' + mod.STATUS_MARKER)
        self.assertEqual(got['id'], 9)
        self.assertIn('--paginate', gh.calls[0][0])
        self.assertIn('--slurp', gh.calls[0][0])

    def test_a_status_comment_this_module_renders_is_recognized(self):
        body, truncated = mod.status_body(SHA, [('tests/x.py', 7, 'x == "1"')])
        self.assertFalse(truncated)
        _, got = self.found(body)
        self.assertEqual(got['id'], 9)

    def test_a_marker_mid_line_is_not_a_marker(self):
        _, got = self.found(f'before {mod.STATUS_MARKER} after')
        self.assertIsNone(got)

    def test_a_marker_inside_quoted_text_is_not_a_marker(self):
        _, got = self.found(f'A quote of a candidate:\n```\nx  # {mod.STATUS_MARKER}\n```')
        self.assertIsNone(got)

    def test_a_non_bot_author_is_not_the_action(self):
        _, got = self.found('A screen report.\n\n' + mod.STATUS_MARKER, login=BOT, user_type='User')
        _, got_too = self.found('A screen report.\n\n' + mod.STATUS_MARKER, login='someone-else')
        self.assertIsNone(got)
        self.assertIsNone(got_too)

    def test_the_wrong_marker_does_not_recognize_a_comment(self):
        _, got = self.found('A review-comment marker only.\n\n<!-- pin-guard: review -->')
        self.assertIsNone(got)


class StatusBody(unittest.TestCase):
    """status_body: the visible report and the machine-readable hidden block."""

    def test_the_visible_part_names_the_head_count_and_each_candidate(self):
        body, truncated = mod.status_body(SHA, [('tests/x.py', 7, 'x == "1"'),
                                                ('tests/y.py', 3, 'y == "2"')])
        self.assertFalse(truncated)
        self.assertIn(f'head {SHA}', body)
        self.assertIn('2 candidate line(s)', body)
        self.assertIn('- ' + mod.inert('tests/x.py:7'), body)
        self.assertIn('- ' + mod.inert('tests/y.py:3'), body)

    def test_a_hostile_path_is_inert_in_the_visible_list_and_exact_in_the_block(self):
        # A path holding an @mention and backticks cannot ping or break the
        # rendering: the visible row sits in a code span its own backtick
        # run cannot close, and the hidden block round-trips the raw path.
        hostile = 'tests/@user`x`.py'
        body, _ = mod.status_body(SHA, [(hostile, 1, 'x == "1"')])
        self.assertIn('- ' + mod.inert(f'{hostile}:1'), body)
        self.assertEqual(mod.parse_block(body)['candidates'], [[hostile, 1, 'x == "1"']])

    def test_a_backtick_led_path_cannot_escape_its_span(self):
        # A path STARTING with a backtick would merge with the opening fence
        # into a run nothing closes; the span pads one space each side so
        # CommonMark strips them and the text stays inert. Verified against
        # `gh api markdown -f mode=gfm`: the unpadded form renders a live
        # user-mention link, the padded form renders <code> and no link.
        hostile = '` @octocat x/tests/test_a.py'
        body, _ = mod.status_body(SHA, [(hostile, 1, 'x == "1"')])
        self.assertEqual(mod.inert(f'{hostile}:1'), '`` ` @octocat x/tests/test_a.py:1 ``')
        self.assertIn('- ' + mod.inert(f'{hostile}:1'), body)
        self.assertEqual(mod.parse_block(body)['candidates'], [[hostile, 1, 'x == "1"']])

    def test_a_clean_head_says_so(self):
        body, truncated = mod.status_body(SHA, [], clean=True)
        self.assertFalse(truncated)
        self.assertIn(f'head {SHA}', body)
        self.assertIn('This head is clean: no candidate test lines.', body)

    def test_a_failed_screen_says_so(self):
        detail = 'the screen exceeded its 240s budget and was killed'
        body, truncated = mod.status_body(SHA, [], error=detail)
        self.assertFalse(truncated)
        self.assertIn('The screen could not run:', body)
        self.assertIn(detail, body)
        self.assertEqual(mod.parse_block(body),
                         {'head_sha': SHA, 'status': 'could-not-screen', 'count': 0,
                          'candidates': [], 'reason': detail})

    def test_the_clean_and_could_not_screen_blocks_differ(self):
        # The block is the surface a consumer reads before a merge: a run
        # that screened nothing must never parse as clean.
        clean, _ = mod.status_body(SHA, [], clean=True)
        failed, _ = mod.status_body(SHA, [], error='exit 1 with no candidate rows')
        clean_block = mod.parse_block(clean)
        failed_block = mod.parse_block(failed)
        self.assertEqual(clean_block['status'], 'clean')
        self.assertEqual(failed_block['status'], 'could-not-screen')
        self.assertNotEqual(clean_block, failed_block)
        self.assertEqual(failed_block['reason'], 'exit 1 with no candidate rows')

    def test_a_delivery_failure_keeps_the_candidate_list(self):
        # The delivery failure adds its own sentence; the path:line list of
        # the screen's findings is still rendered above it.
        body, _ = mod.status_body(SHA, [('tests/x.py', 7, 'x == "1"')],
                                  error='the review could not be posted: gh: HTTP 502')
        self.assertIn('- ' + mod.inert('tests/x.py:7'), body)
        self.assertIn('the review could not be posted: gh: HTTP 502', body)

    def test_the_hidden_block_carries_the_full_candidate_list(self):
        candidates = [('tests/x.py', 7, 'x == "1"'), ('tests/y.py', 3, 'y == "2"')]
        body, _ = mod.status_body(SHA, candidates)
        parsed = mod.parse_block(body)
        self.assertEqual(parsed, {'head_sha': SHA, 'status': 'candidates', 'count': 2, 'candidates': [
            ['tests/x.py', 7, 'x == "1"'], ['tests/y.py', 3, 'y == "2"']]})

    def test_the_hidden_block_is_escaped_against_its_own_markup(self):
        candidates = [('tests/<script>.py', 7, '--> <b>bold</b> &lt;verbatim&gt; x & y -1 --'),
                      ('tests/y.py', 3, '```` nested ``` ticks ````')]
        body, truncated = mod.status_body(SHA, candidates)
        self.assertFalse(truncated)
        hidden = block_of(body)
        # The escaped JSON can hold no '<', '>' or '-': the sequence '-->'
        # closes the block's own comment and appears nowhere else after the
        # marker, and no markup can leak out of the block. The escapes are
        # plain JSON unicode escapes (<, >, -) a parser reads
        # directly — no HTML entity survives.
        after_marker = body.rpartition(f'\n{mod.STATUS_MARKER}\n')[2]
        self.assertEqual(after_marker.count('-->'), 1)
        for char in '<>-':
            self.assertNotIn(char, hidden)
        self.assertIn('\\u002d', hidden)
        # And the consumer's read of the block round-trips every row exactly.
        self.assertEqual(mod.parse_block(body), {'head_sha': SHA, 'status': 'candidates', 'count': 2, 'candidates': [
            ['tests/<script>.py', 7, '--> <b>bold</b> &lt;verbatim&gt; x & y -1 --'],
            ['tests/y.py', 3, '```` nested ``` ticks ````']]})

    def test_a_body_over_the_comment_limit_cuts_the_visible_list_and_states_the_omissions(self):
        # Paths long enough that the visible list pushes the body over the
        # limit while the full hidden list still fits: the block keeps every
        # candidate and the visible list shows what fits.
        candidates = [(f'tests/{"a" * 700}_{n}.py', n, 'x == "1"') for n in range(50)]
        body, truncated = mod.status_body(SHA, candidates)
        self.assertTrue(truncated)
        self.assertGreater(len(body), mod.LIMIT - 2000)
        visible = body.rpartition(f'\n{mod.STATUS_MARKER}\n')[0]
        shown = [line for line in visible.splitlines() if line.startswith('- ')]
        self.assertLess(len(shown), 50)
        self.assertGreater(len(shown), 0)
        self.assertIn('candidate line(s) omitted', visible)
        self.assertIn('50 candidate line(s)', visible)
        parsed = mod.parse_block(body)
        self.assertEqual(parsed['count'], 50)
        self.assertEqual(len(parsed['candidates']), 50)
        self.assertEqual(parsed['candidates'][7], [f'tests/{"a" * 700}_7.py', 7, 'x == "1"'])

    def test_a_hidden_list_that_cannot_fit_leaves_a_count_only_block(self):
        # Candidate texts so long that the full hidden list alone would exceed
        # the comment limit: the block states the count and truncates.
        candidates = [(f'tests/x{n}.py', n, 't' * 3000) for n in range(40)]
        body, truncated = mod.status_body(SHA, candidates)
        self.assertTrue(truncated)
        parsed = mod.parse_block(body)
        self.assertEqual(parsed['count'], 40)
        self.assertTrue(parsed['truncated'])
        self.assertNotIn('candidates', parsed)
        visible = body.rpartition(f'\n{mod.STATUS_MARKER}\n')[0]
        self.assertIn('40 candidate line(s)', visible)
        self.assertGreaterEqual(len([line for line in visible.splitlines() if line.startswith('- ')]), 1)


class UpsertStatus(unittest.TestCase):
    """upsert_status: edit in place, create when absent, nothing when clean and none."""

    def test_an_existing_status_comment_is_edited_in_place(self):
        body, _ = mod.status_body(SHA, [('tests/x.py', 7, 'x == "1"')])
        gh = Gh(listing_call([comment(9, 'an old question'), comment(55, 'A screen report.\n\n' + mod.STATUS_MARKER)]),
                (('api', f'repos/{REPO}/issues/{PR}/comments/55', '--method', 'PATCH', '--input', '-'),
                 payload({'body': body}), json.dumps({'html_url': 'https://example.com/c/55'})))
        got = mod.upsert_status(gh, REPO, PR, SHA, [('tests/x.py', 7, 'x == "1"')])
        self.assertEqual(got, (True, 'https://example.com/c/55', False))
        # Only the listing read and the one edit: no second write of any kind.
        self.assertEqual(len(gh.calls), 2)

    def test_a_clean_head_edits_its_earlier_status_comment(self):
        body, _ = mod.status_body(SHA, [], clean=True)
        gh = Gh(listing_call([comment(55, 'A screen report.\n\n' + mod.STATUS_MARKER)]),
                (('api', f'repos/{REPO}/issues/{PR}/comments/55', '--method', 'PATCH', '--input', '-'),
                 payload({'body': body}), json.dumps({'html_url': 'https://example.com/c/55'})))
        got = mod.upsert_status(gh, REPO, PR, SHA, [], clean=True)
        self.assertEqual(got, (True, 'https://example.com/c/55', False))

    def test_a_clean_head_with_no_earlier_status_comment_posts_nothing(self):
        gh = Gh(listing_call([]))
        got = mod.upsert_status(gh, REPO, PR, SHA, [], clean=True)
        self.assertEqual(got, (False, '', False))
        # The control: the double has no modeled write, so any POST or PATCH
        # attempt would have raised; exactly the one read happened.
        self.assertEqual(len(gh.calls), 1)

    def test_a_status_comment_is_created_when_absent(self):
        body, _ = mod.status_body(SHA, [('tests/x.py', 7, 'x == "1"')])
        gh = Gh(listing_call([]),
                (('api', f'repos/{REPO}/issues/{PR}/comments', '--method', 'POST', '--input', '-'),
                 payload({'body': body}), json.dumps({'html_url': 'https://example.com/c/56'})))
        got = mod.upsert_status(gh, REPO, PR, SHA, [('tests/x.py', 7, 'x == "1"')],
                                error=None)
        self.assertEqual(got, (True, 'https://example.com/c/56', False))

    def test_a_failed_screen_creates_its_status_comment(self):
        body, _ = mod.status_body(SHA, [], error='boom')
        gh = Gh(listing_call([]),
                (('api', f'repos/{REPO}/issues/{PR}/comments', '--method', 'POST', '--input', '-'),
                 payload({'body': body}), json.dumps({'html_url': 'https://example.com/c/57'})))
        got = mod.upsert_status(gh, REPO, PR, SHA, [], error='boom')
        self.assertEqual(got, (True, 'https://example.com/c/57', False))

    def test_a_truncated_status_comment_reports_its_truncation(self):
        # One candidate whose path alone busts the comment limit: the block
        # truncates and the boolean travels out of the upsert.
        def truncated_body(stdin):
            assert stdin is not None
            assert mod.parse_block(json.loads(stdin)['body'])['truncated'] is True
            return True

        gh = Gh(listing_call([]),
                (('api', f'repos/{REPO}/issues/{PR}/comments', '--method', 'POST', '--input', '-'),
                 truncated_body, json.dumps({'html_url': 'https://example.com/c/58'})))
        got = mod.upsert_status(gh, REPO, PR, SHA, [('tests/' + 'a' * 70000 + '.py', 1, 'x == "1"')])
        self.assertEqual(got[0], True)
        self.assertEqual(got[2], True)


if __name__ == '__main__':
    unittest.main()
