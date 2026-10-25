"""screen_run.run_screen: the child screen's exit reduced to a three-valued outcome."""
import importlib.util
import os
import signal
import subprocess
import sys
import unittest
from unittest import mock

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location('screen_run', os.path.join(KIT, 'scripts', 'ci', 'screen_run.py'))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

REPO, PR, TOKEN = 'o/r', 9, 'tok-en'
# The exact argv the screen is spawned with, derived here from the checkout root.
SCREEN = [sys.executable, '-I', os.path.join(KIT, 'check_pin_tests.py'), '--repo', REPO, '--pr', str(PR)]
ENV_NAMES = ('PATH', 'HOME', 'LANG', 'LC_ALL')


class Seam:
    """A runner double modeling one screen invocation; a second call or another timeout is refused."""

    def __init__(self, code, out, err, timeout=240):
        self.model = (code, out, err)
        self.timeout = timeout
        self.argv = None
        self.calls = 0

    def __call__(self, argv, timeout):
        if self.calls:
            raise AssertionError(f'unmodeled second screen invocation {argv}')
        self.calls += 1
        if timeout != self.timeout:
            raise AssertionError(f'unmodeled screen timeout {timeout}, modeled {self.timeout}')
        self.argv = argv
        return self.model


class HangSeam:
    """A runner double whose one modeled invocation hangs past its budget."""

    def __init__(self, timeout=240):
        self.timeout = timeout

    def __call__(self, argv, timeout):
        if timeout != self.timeout:
            raise AssertionError(f'unmodeled screen timeout {timeout}, modeled {self.timeout}')
        raise subprocess.TimeoutExpired(argv[0], timeout)


class RunScreen(unittest.TestCase):
    """run_screen over the runner seam: every contract row of the child's exit."""

    def screen(self, code, out, err):
        seam = Seam(code, out, err)
        return seam, mod.run_screen(REPO, PR, token=TOKEN, runner=seam)

    def test_exit_0_is_clean(self):
        seam, got = self.screen(0, '', '')
        self.assertEqual(got, mod.ScreenOutcome('clean', (), ''))
        self.assertEqual(seam.argv, SCREEN)

    def test_exit_1_rows_are_the_candidates(self):
        out = ('tests/deploy/container-image.test.ts:23: const NODE_BASE_TAG = "node:24.21.0-bookworm-slim";\n'
               'tests/test_wf.py:7: assert a == "b", "see c:12: d"\n')
        seam, got = self.screen(1, out, '')
        self.assertEqual(got, mod.ScreenOutcome('candidates', (
            ('tests/deploy/container-image.test.ts', 23, 'const NODE_BASE_TAG = "node:24.21.0-bookworm-slim";'),
            ('tests/test_wf.py', 7, 'assert a == "b", "see c:12: d"')), ''))
        self.assertEqual(seam.argv, SCREEN)

    def test_exit_1_without_a_parseable_row_is_not_candidates(self):
        # A wrapper must never read exit 1 alone as candidates.
        seam, got = self.screen(1, 'screen noise with no row shape\n', '')
        self.assertEqual(got, mod.ScreenOutcome('could-not-screen', (), 'exit 1 with no candidate rows'))
        self.assertEqual(seam.argv, SCREEN)

    def test_exit_2_reports_the_stderr_tail(self):
        err = 'could not screen package.json: not valid JSON (Expecting value...)\n' * 30
        _, got = self.screen(2, '', err)
        self.assertEqual(got.status, 'could-not-screen')
        self.assertEqual(got.detail, err.strip()[-500:])
        # A short stderr is the whole reason, unbounded and unstropped inside.
        _, got = self.screen(2, '', 'could not screen Dockerfile: HTTP 404: Not Found\n')
        self.assertEqual(got.detail, 'could not screen Dockerfile: HTTP 404: Not Found')

    def test_a_signal_names_the_signal(self):
        _, got = self.screen(-9, '', '')
        self.assertEqual(got.status, 'could-not-screen')
        self.assertIn('signal 9', got.detail)
        self.assertIn('SIGKILL', got.detail)

    def test_a_timeout_names_the_budget(self):
        got = mod.run_screen(REPO, PR, token=TOKEN, runner=HangSeam())
        self.assertEqual(got.status, 'could-not-screen')
        self.assertIn('240s budget', got.detail)
        # The named budget is the caller's, not a hardcoded 240.
        got = mod.run_screen(REPO, PR, token=TOKEN, runner=HangSeam(60), timeout_s=60)
        self.assertIn('60s budget', got.detail)

    def test_any_other_nonzero_exit_reports_the_stderr_tail(self):
        _, got = self.screen(3, '', 'gh: an unexpected failure\n')
        self.assertEqual(got, mod.ScreenOutcome('could-not-screen', (), 'gh: an unexpected failure'))

    def test_the_seam_receives_the_callers_budget(self):
        seam = Seam(0, '', '', timeout=60)
        mod.run_screen(REPO, PR, token=TOKEN, runner=seam, timeout_s=60)
        self.assertEqual(seam.argv, SCREEN)


def popen_double(record, code=None, out='', err=''):
    """A Popen double modeling one screen child (code None models a hang); the rest is refused."""
    record = record

    class Popen:
        pid = 424242

        def __init__(self, argv, **kwargs):
            if set(kwargs) != {'env', 'stdout', 'stderr', 'text', 'start_new_session'}:
                raise AssertionError(f'unmodeled Popen arguments {sorted(kwargs)}')
            record.update(argv=argv, **kwargs)
            self.communicates = 0
            self.returncode = None

        def communicate(self, timeout=None):
            self.communicates += 1
            if code is None:  # the hang: a timed-out drain, then the post-kill drain
                if self.communicates == 1:
                    if timeout != 240:
                        raise AssertionError(f'unmodeled communicate timeout {timeout}')
                    raise subprocess.TimeoutExpired(record['argv'][0], timeout)
                if self.communicates == 2 and timeout is None:
                    return '', ''
            elif self.communicates == 1 and timeout == 240:
                self.returncode = code
                return out, err
            raise AssertionError(f'unmodeled communicate {self.communicates} timeout={timeout}')

    return Popen


class DefaultRunner(unittest.TestCase):
    """The default runner: the argv and allowlisted env the child is spawned with, and the kill on timeout."""

    def test_the_child_gets_the_screen_argv_and_an_allowlisted_env(self):
        record, called = {}, []
        with mock.patch('subprocess.Popen', popen_double(record, code=0)), \
                mock.patch('os.killpg', lambda *a: called.append(a)):
            got = mod.run_screen(REPO, PR, token=TOKEN)
        self.assertEqual(got, mod.ScreenOutcome('clean', (), ''))
        self.assertEqual(record['argv'], SCREEN)
        self.assertTrue(record['start_new_session'])
        self.assertIs(record['stdout'], subprocess.PIPE)
        self.assertIs(record['stderr'], subprocess.PIPE)
        self.assertTrue(record['text'])
        env = record['env']
        self.assertEqual(env['GH_TOKEN'], TOKEN)
        self.assertEqual(env['GH_REPO'], REPO)
        self.assertEqual(set(env), {'GH_TOKEN', 'GH_REPO'} | {n for n in ENV_NAMES if n in os.environ})
        for name in ENV_NAMES:
            if name in os.environ:
                self.assertEqual(env[name], os.environ[name])
        self.assertEqual(called, [])

    def test_a_hanging_child_loses_its_whole_process_group(self):
        record, called = {}, []
        with mock.patch('subprocess.Popen', popen_double(record)), \
                mock.patch('os.killpg', lambda *a: called.append(a)):
            got = mod.run_screen(REPO, PR, token=TOKEN)
        self.assertEqual(got.status, 'could-not-screen')
        self.assertIn('240s budget', got.detail)
        self.assertEqual(called, [(424242, signal.SIGKILL)])
