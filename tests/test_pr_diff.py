"""pr_diff.read_diff past GitHub's 406: the diff and the head's files from a real git fetch of merge base and head."""
import importlib.util
import json
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location('check_pin_tests', os.path.join(KIT, 'check_pin_tests.py'))
assert spec and spec.loader
screen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(screen)
mod = importlib.import_module('pr_diff')


class ReadDiff(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = cls.enterClassContext(tempfile.TemporaryDirectory())
        repo, cls.remote = pathlib.Path(root, 'o', 'r'), f'file://{root}/{{}}'
        env = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_AUTHOR_NAME': 't',
               'GIT_AUTHOR_EMAIL': 't@example.com', 'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@example.com'}

        def git(*args):
            return subprocess.run(['git', '-C', repo, *args], check=True, text=True, capture_output=True, env=env).stdout

        def commit(branch, files):
            git('checkout', '-q', '-b', branch, *([cls.merge_base] if branch != 'start' else []))
            for path, text in files.items():
                if text:
                    (repo / path).write_text(text, encoding='utf-8')
                else:
                    (repo / path).unlink()
            git('add', '-A')
            git('commit', '-qm', branch)
            return git('rev-parse', 'HEAD').strip()
        repo.mkdir(parents=True)
        git('init', '-q')
        cls.merge_base = commit('start', {'a.py': 'a\n', 'gone.py': 'g\n'})
        cls.head = commit('head', {'a.py': 'a\nb\n', 'new.py': 'n\n', 'gone.py': '', 'moved.py': 'g\n'})
        cls.base = commit('main', {'gone.py': 'G\n'})  # the base branch moved past the merge base

    def gh(self, served=None, refusal='HTTP 406: Sorry, the diff exceeded the maximum number of files (300).', merge_base=None):
        """A gh double for one pull request; a call it does not model raises KeyError."""
        answers = {('pr', 'view', '9', '-R', 'o/r', '--json', 'headRefOid'): json.dumps({'headRefOid': self.head}),
                   ('pr', 'view', '9', '-R', 'o/r', '--json', 'baseRefOid'): json.dumps({'baseRefOid': self.base}),
                   ('api', f'repos/o/r/compare/{self.base}...{self.head}?per_page=1'):
                       json.dumps({'merge_base_commit': {'sha': merge_base or self.merge_base}}),
                   ('auth', 'token'): 'tok\n', ('pr', 'diff', '9', '-R', 'o/r'): served}

        def fake(*args):
            if answers[args] is None:
                raise subprocess.CalledProcessError(1, ['gh'], stderr=refusal)
            return answers[args]
        return fake

    def setUp(self):
        self.enterContext(mock.patch.object(mod, 'REMOTE', self.remote))

    def test_a_refused_diff_is_the_merge_base_to_head_diff_with_the_heads_files(self):
        head, diff_text, contents = mod.read_diff(self.gh(), 'o/r', 9)
        self.assertEqual([(p, n, t) for p, n, k, t in screen._lines(diff_text) if k == '+'],
                         [('a.py', 2, 'b'), ('new.py', 1, 'n')])
        self.assertEqual((head, contents), (self.head, {'a.py': 'a\nb\n', 'moved.py': 'g\n', 'new.py': 'n\n'}))

    def test_a_served_diff_is_kept_and_any_other_refusal_raised_without_a_fetch(self):
        self.assertEqual(mod.read_diff(self.gh(served='diff'), 'o/r', 9), (self.head, 'diff', {}))
        with self.assertRaises(subprocess.CalledProcessError) as caught:
            mod.read_diff(self.gh(refusal='HTTP 502: Bad Gateway'), 'o/r', 9)
        self.assertEqual(caught.exception.stderr, 'HTTP 502: Bad Gateway')

    def test_a_failed_fetch_is_raised_never_an_empty_diff(self):
        with self.assertRaisesRegex(subprocess.CalledProcessError, "'fetch'"):
            mod.read_diff(self.gh(merge_base='0' * 40), 'o/r', 9)
