"""release_dispatch's admission rules, replayed against named API answers.

Every GitHub surface goes through the injected `read` and `write` seams, each
refusing an endpoint it does not model, so a release that would bypass a
refusal cannot pass by the double answering something else.
"""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from functools import partial
from types import SimpleNamespace
from unittest import mock

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    'release_dispatch', os.path.join(KIT, 'scripts', 'ci', 'release_dispatch.py'))
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

REPO = 'owner/repo'
SHA = 'a' * 40


class Api:
    """Replay only named GitHub endpoints; an unexpected read fails the test."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, argv):
        assert argv[:2] == ['gh', 'api'], argv
        assert 'Cache-Control: no-cache' in argv, argv
        endpoint = argv[2]
        assert endpoint in self.responses, argv
        self.calls.append(argv)
        value = self.responses[endpoint]
        return json.dumps(value() if callable(value) else value)


class Writer:
    """Record GitHub write argv and return the annotated object's distinct SHA."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv):
        self.calls.append(argv)
        return json.dumps({'sha': 'c' * 40})


class FailingWriter(Writer):
    """A writer whose `fail_at`-th call raises, as a refused GitHub write does."""

    def __init__(self, fail_at):
        super().__init__()
        self.fail_at = fail_at

    def __call__(self, argv):
        result = super().__call__(argv)
        if len(self.calls) == self.fail_at:
            raise subprocess.CalledProcessError(
                1, argv, stderr='HTTP 422 already exists')
        return result


class Clock:
    """Replace only the elapsed-time boundary, never the admission logic."""

    def __init__(self):
        self.elapsed = 0
        self.sleeps = []

    def now(self):
        return self.elapsed

    def sleep(self, seconds):
        assert seconds > 0
        self.sleeps.append(seconds)
        self.elapsed += seconds


def _rules(contexts=('required-one', 'required-two')):
    return {'id': 7, 'name': 'require-ci-aggregate', 'enforcement': 'active',
            'rules': [{'type': 'required_status_checks', 'parameters': {
                'required_status_checks': [{'context': name} for name in contexts]}}]}


def _run(suite=1, event='push', actor='human', sha=SHA, conclusion='failure'):
    return {'check_suite_id': suite, 'event': event, 'actor': {'login': actor},
            'head_sha': sha, 'status': 'completed', 'conclusion': conclusion}


def _check(name='shellcheck', suite=1, conclusion: str | None = 'success',
           status='completed', sha=SHA):
    return {'name': name, 'check_suite': {'id': suite}, 'conclusion': conclusion,
            'status': status, 'head_sha': sha}


def _gate_api(runs, checks):
    return Api({f'repos/{REPO}/actions/runs?head_sha={SHA}': runs,
                f'repos/{REPO}/commits/{SHA}/check-runs?filter=all': checks})


def _verify_api(sha, contexts=('shellcheck',)):
    api = _gate_api([{'workflow_runs': [_run(sha=sha)]}],
                    [{'check_runs': [_check(sha=sha)]}])
    api.responses = {key.replace(SHA, sha): value for key, value in api.responses.items()}
    api.responses.update({
        f'repos/{REPO}/branches/main': {'commit': {'sha': sha}},
        f'repos/{REPO}/tags': [[{'name': 'v2.10.0'}]],
        f'repos/{REPO}/rulesets': [[_rules()]],
        f'repos/{REPO}/rulesets/7': _rules(contexts)})
    return api


def _head():
    return subprocess.run(['git', 'rev-parse', 'HEAD'], check=True,
                          capture_output=True, text=True).stdout.strip()


class Case(unittest.TestCase):
    """One release-dispatch behaviour; `refuses` is the shared refusal assertion."""

    def refuses(self, operation, message):
        try:
            operation()
        except mod.ReleaseError as error:
            self.assertIn(message, str(error))
        else:
            self.fail(f'expected refusal: {message}')


class Version(Case):
    def test_version_requires_canonical_three_component_semver(self):
        self.assertEqual(mod.version_tuple('2.10.0'), (2, 10, 0))
        for version in ('v2.1.1', '2.1', '2.1.1-rc.1', '2.1.1+build', '',
                        ' 2.1.1', '2.1.1\n', '02.1.1', '2.01.1', '2.1.01',
                        '２.1.1', '-2.1.1', '2.1.1; echo bad'):
            self.refuses(partial(mod.version_tuple, version), 'version')

    def test_numeric_latest_tag_allows_exactly_three_successors(self):
        histories = (
            ([[{'name': 'v2.9.0'}, {'name': 'v99.0.0-rc.1'}],
              [{'name': 'v2.10.0'}, {'name': 'not-a-release'}]],
             ('2.10.1', '2.11.0', '3.0.0'),
             ('7.4.7', '7.5.0', '8.0.0', '2.10.0', '2.9.1', '2.10.2',
              '2.11.1', '3.0.1', '4.0.0')),
            ([[{'name': 'v7.4.5'}], [{'name': 'v7.4.6'}]],
             ('7.4.7', '7.5.0', '8.0.0'),
             ('2.10.1', '2.11.0', '3.0.0', '7.4.6', '7.4.8', '7.5.1', '8.0.1', '9.0.0')),
        )
        for pages, accepted, refused in histories:
            for version in refused:
                api = Api({f'repos/{REPO}/tags': pages})
                self.refuses(partial(mod.validate_version, api, REPO, version), 'successor')
            for version in accepted:
                api = Api({f'repos/{REPO}/tags': pages})
                mod.validate_version(api, REPO, version)
                self.assertIn('--paginate', api.calls[0])
                self.assertIn('--slurp', api.calls[0])

    def test_version_refuses_missing_releases_and_rejects_shape_before_reads(self):
        api = Api({f'repos/{REPO}/tags': [[{'name': 'v1.0.0-preview'}], []]})
        self.refuses(lambda: mod.validate_version(api, REPO, '1.0.0'), 'no vX.Y.Z')
        api = Api({})
        self.refuses(lambda: mod.validate_version(api, REPO, 'v1.0.0'), 'version')
        self.assertEqual(api.calls, [])


class RequiredContexts(Case):
    def test_required_contexts_come_from_the_active_named_ruleset_on_later_pages(self):
        api = Api({f'repos/{REPO}/rulesets': [[{'id': 4, 'name': 'unrelated'}], [_rules()]],
                   f'repos/{REPO}/rulesets/7': _rules(('a-new-context',))})
        self.assertEqual(mod.required_contexts(api, REPO), {'a-new-context'})
        self.assertIn('--paginate', api.calls[0])
        self.assertIn('--slurp', api.calls[0])

    def test_missing_inactive_or_empty_required_ruleset_refuses(self):
        for rows, detail in (([], _rules()), ([_rules()], _rules(())),
                             ([_rules()], {**_rules(), 'enforcement': 'disabled'}),
                             ([{**_rules(), 'enforcement': 'evaluate'}], _rules()),
                             ([_rules(), _rules()], _rules()),
                             ([_rules()], {**_rules(), 'rules': []})):
            api = Api({f'repos/{REPO}/rulesets': [rows], f'repos/{REPO}/rulesets/7': detail})
            self.refuses(partial(mod.required_contexts, api, REPO), 'require-ci-aggregate')


class Wait(Case):
    def wait(self, api, *, required=None, timeout=60, grace=40, clock=None):
        clock = clock or Clock()
        frozen = SimpleNamespace(monotonic=clock.now, sleep=clock.sleep)
        with mock.patch.object(mod, 'time', frozen):
            mod.wait(api, REPO, SHA, required or {'shellcheck'},
                     timeout=timeout, grace=grace, interval=20)
        return clock

    def test_push_check_success_counts_even_when_its_workflow_reports_failure(self):
        api = _gate_api([{'workflow_runs': [_run()]}], [{'check_runs': [_check()]}])
        self.assertEqual(self.wait(api).sleeps, [])
        for argv in api.calls:
            self.assertIn('--paginate', argv)
            self.assertIn('--slurp', argv)

    def test_other_events_dependabot_unmapped_and_wrong_sha_never_satisfy(self):
        for run, check in ((None, _check()),
                           (_run(event='schedule'), _check()),
                           (_run(event='workflow_run'), _check()),
                           (_run(event='issue_comment'), _check()),
                           (_run(event='pull_request'), _check()),
                           (_run(event='workflow_dispatch'), _check()),
                           (_run(actor='dependabot[bot]'), _check()),
                           (_run(sha='b' * 40), _check()),
                           (_run(suite=2), _check()),
                           (_run(), _check(sha='b' * 40))):
            api = _gate_api([{'workflow_runs': [run] if run else []}],
                            [{'check_runs': [check]}])
            clock = Clock()
            self.refuses(partial(self.wait, api, clock=clock), 'registration grace')
            self.assertEqual(clock.elapsed, 40)
            self.assertEqual(clock.sleeps, [20, 20])

    def test_scheduled_non_success_cannot_poison_push_success_on_the_same_sha(self):
        for conclusion in ('failure', 'cancelled', 'skipped', 'neutral', 'success'):
            api = _gate_api([{'workflow_runs': [_run(suite=2, event='schedule')]},
                             {'workflow_runs': [_run()]}],
                            [{'check_runs': [_check(suite=2, conclusion=conclusion)]},
                             {'check_runs': [_check()]}])
            self.assertEqual(self.wait(api).sleeps, [])

    def test_each_completed_non_success_refuses_immediately_even_beside_success(self):
        for conclusion in ('failure', 'cancelled', 'skipped', 'neutral', 'timed_out',
                           'action_required', 'stale', 'startup_failure', None):
            api = _gate_api([{'workflow_runs': [_run(conclusion='success')]}],
                            [{'check_runs': [_check()]},
                             {'check_runs': [_check(conclusion=conclusion)]}])
            clock = Clock()
            self.refuses(partial(self.wait, api, clock=clock), f'shellcheck: {conclusion}')
            self.assertEqual(clock.sleeps, [])

    def test_every_required_context_must_register_not_just_one(self):
        api = _gate_api([{'workflow_runs': [_run()]}], [{'check_runs': [_check()]}])
        self.refuses(lambda: self.wait(api, required={'shellcheck', 'new-context'}),
                     'new-context')

    def test_unrelated_failed_check_does_not_block_required_success(self):
        api = _gate_api([{'workflow_runs': [_run()]}], [{'check_runs': [
            _check(), _check(name='informational', conclusion='failure')]}])
        self.assertEqual(self.wait(api).sleeps, [])

    def test_pending_check_waits_until_its_own_success(self):
        clock = Clock()
        api = _gate_api([{'workflow_runs': [_run()]}], lambda: [{'check_runs': [
            _check(conclusion=None, status='in_progress')
            if clock.elapsed < 40 else _check()]}])
        self.assertEqual(self.wait(api, clock=clock).sleeps, [20, 20])

    def test_missing_check_can_register_during_the_grace(self):
        clock = Clock()
        api = _gate_api([{'workflow_runs': [_run()]}], lambda: [{'check_runs': [
            _check()] if clock.elapsed else []}])
        self.assertEqual(self.wait(api, clock=clock).sleeps, [20])

    def test_pending_and_absent_checks_have_separate_bounded_deadlines(self):
        for checks, message in (([], 'registration grace'),
                                ([_check(status='queued', conclusion=None)], 'deadline')):
            api = _gate_api([{'workflow_runs': [_run()]}], [{'check_runs': checks}])
            clock = Clock()
            self.refuses(partial(self.wait, api, timeout=65, grace=35, clock=clock),
                         message)
            self.assertEqual(clock.elapsed, 35 if not checks else 65)
            self.assertEqual(clock.sleeps, [20, 15] if not checks else [20, 20, 20, 5])

    def test_api_errors_and_malformed_json_refuse_without_sleep(self):
        def broken(_argv):
            raise OSError('network read refused')

        self.refuses(lambda: self.wait(broken), 'cannot read repos/owner/repo/actions/runs')
        self.refuses(lambda: self.wait(lambda _argv: 'not JSON'), 'cannot read')


class Verify(Case):
    def clocked_verify(self, contexts):
        printed, clock = io.StringIO(), Clock()
        frozen = SimpleNamespace(monotonic=clock.now, sleep=clock.sleep)
        with mock.patch.object(mod, 'time', frozen), \
                contextlib.redirect_stdout(printed):
            mod.verify(_verify_api(_head(), contexts), REPO, '2.10.1')
        return printed.getvalue()

    def test_verify_pins_main_before_other_reads_and_returns_only_that_sha(self):
        sha = _head()
        api = _verify_api(sha)
        self.assertEqual(mod.verify(api, REPO, '2.10.1'), sha)
        self.assertEqual(api.calls[0][2], f'repos/{REPO}/branches/main')
        self.assertEqual([argv[2] for argv in api.calls].count(
            f'repos/{REPO}/branches/main'), 1)

    def test_verify_skips_only_the_pull_request_gate_and_names_it(self):
        self.assertEqual(mod.PULL_REQUEST_ONLY, {'gate'})
        printed = self.clocked_verify(('shellcheck', 'gate'))
        self.assertIn('skipping pull-request-only required context: gate', printed)
        self.assertNotIn('skipping', self.clocked_verify(('shellcheck',)))

    def test_verify_still_refuses_a_missing_push_context_beside_the_gate(self):
        near_miss = 'aggregate (extra)'
        self.refuses(partial(self.clocked_verify,
                             ('shellcheck', 'gate', 'aggregate', near_miss)),
                     f"registration grace: ['aggregate', '{near_miss}']")
        self.refuses(partial(self.clocked_verify, ('gate',)), 'no push-checked contexts')

    def test_verify_refuses_a_main_that_moved_past_the_checkout(self):
        api = Api({f'repos/{REPO}/branches/main': {'commit': {'sha': 'f' * 40}}})
        self.refuses(partial(mod.verify, api, REPO, '2.10.1'), 'main moved')


class Publish(Case):
    def test_publish_creates_annotated_object_then_ref_then_generated_release(self):
        api = Api({f'repos/{REPO}/tags': [[{'name': 'v2.10.0'}]]})
        write = Writer()
        mod.publish(api, write, REPO, '2.10.1', SHA)
        self.assertEqual(write.calls, [
            ['gh', 'api', f'repos/{REPO}/git/tags', '-X', 'POST',
             '-f', 'tag=v2.10.1', '-f', 'message=Release v2.10.1',
             '-f', f'object={SHA}', '-f', 'type=commit'],
            ['gh', 'api', f'repos/{REPO}/git/refs', '-X', 'POST',
             '-f', 'ref=refs/tags/v2.10.1', '-f', 'sha=' + 'c' * 40],
            ['gh', 'api', f'repos/{REPO}/releases', '-X', 'POST',
             '-f', 'tag_name=v2.10.1', '-F', 'generate_release_notes=true']])

    def test_publish_refuses_bad_version_sha_and_existing_tag_before_any_write(self):
        for version, sha, tags, message in (
                ('v2.10.1', SHA, [], 'version'),
                ('2.10.1', 'not-a-sha', [], 'SHA'),
                ('2.10.1', SHA, [{'name': 'v2.10.1'}], 'already exists')):
            api = Api({f'repos/{REPO}/tags': [[], tags]})
            write = Writer()
            self.refuses(partial(mod.publish, api, write, REPO, version, sha), message)
            self.assertEqual(write.calls, [])
            if message != 'already exists':
                self.assertEqual(api.calls, [])

    def test_publish_write_errors_name_the_attempt_and_stop_later_writes(self):
        for fail_at, attempt in ((1, 'annotated tag v2.10.1'),
                                 (2, 'tag ref v2.10.1'), (3, 'release v2.10.1')):
            write = FailingWriter(fail_at)

            api = Api({f'repos/{REPO}/tags': [[]]})
            self.refuses(partial(mod.publish, api, write, REPO, '2.10.1', SHA),
                         attempt)
            self.assertEqual(len(write.calls), fail_at)


class Cli(unittest.TestCase):
    def test_cli_writes_pinned_output_only_after_verify_success(self):
        sha = _head()
        errors = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            output = os.path.join(directory, 'output')
            with mock.patch.dict(os.environ, {'GITHUB_OUTPUT': output}), \
                    contextlib.redirect_stderr(errors):
                self.assertEqual(mod.main(['verify', REPO, '2.10.1'],
                                          read=_verify_api(sha), write=Writer()), 0)
                with open(output, encoding='utf-8') as handle:
                    self.assertEqual(handle.read(), f'sha={sha}\n')
                os.unlink(output)
                regex = 'f' * 40
                api = Api({f'repos/{REPO}/branches/main': {'commit': {'sha': regex}}})
                self.assertEqual(mod.main(['verify', REPO, '2.10.1'],
                                          read=api, write=Writer()), 1)
            self.assertFalse(os.path.exists(output))
        self.assertIn('verify', errors.getvalue())
        self.assertIn('2.10.1', errors.getvalue())
        self.assertIn('main moved', errors.getvalue())

    def test_cli_publish_and_malformed_api_data_fail_loudly(self):
        api = Api({f'repos/{REPO}/tags': [[]]})
        write = Writer()
        self.assertEqual(mod.main(['publish', REPO, '2.10.1', SHA], read=api, write=write), 0)
        self.assertEqual(len(write.calls), 3)
        for payload in ({}, {'commit': {'sha': None}}):
            errors = io.StringIO()
            api = Api({f'repos/{REPO}/branches/main': payload})
            with contextlib.redirect_stderr(errors):
                self.assertEqual(mod.main(['verify', REPO, '2.10.1'],
                                          read=api, write=write), 1)
            self.assertIn('verify', errors.getvalue())
            self.assertIn('2.10.1', errors.getvalue())


class Seams(unittest.TestCase):
    def test_command_seams_capture_output_and_propagate_a_process_failure(self):
        for command in (mod._read, mod._write):
            self.assertEqual(command([sys.executable, '-c', 'print("response")']),
                             'response\n')
            with self.assertRaises(subprocess.CalledProcessError) as raised:
                command([sys.executable, '-c', 'raise SystemExit(7)'])
            self.assertEqual(raised.exception.returncode, 7)
