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
        root = cls.root = cls.enterClassContext(tempfile.TemporaryDirectory())
        pathlib.Path(root, 'git').mkdir()
        pathlib.Path(root, 'git', 'attributes').write_text('* -diff\n', encoding='utf-8')  # a global attributes file
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
        git('config', 'uploadpack.allowFilter', 'true')  # so the fetch is blobless and the diff fetches lazily
        git('config', 'uploadpack.allowAnySHA1InWant', 'true')  # as GitHub serves a blob fetched by its SHA
        cls.merge_base = commit('start', {'a.py': 'a\n', 'gone.py': 'g\n'})
        cls.head = commit('head', {'a.py': 'a\nb\n', 'new.py': 'n\n', 'gone.py': '', 'moved.py': 'g\n',
                                   'té.py': 'x = 1\rassert v\n', 'b.py': 'b\0\n'})
        cls.base = commit('main', {'gone.py': 'G\n'})  # the base branch moved past the merge base
        cls.blobs = [git('rev-parse', f'{cls.head}:{path}').strip() for path in ('a.py', 'té.py')]

    def gh(self, served=None, refusal='HTTP 406: Sorry, the diff exceeded the maximum number of files (300).', merge_base=None):
        answers = {('pr', 'view', '9', '-R', 'o/r', '--json', 'headRefOid'): json.dumps({'headRefOid': self.head}),
                   ('pr', 'view', '9', '-R', 'o/r', '--json', 'baseRefOid'): json.dumps({'baseRefOid': self.base}),
                   ('api', f'repos/o/r/compare/{self.base}...{self.head}?per_page=1'):
                       json.dumps({'merge_base_commit': {'sha': merge_base or self.merge_base}}),
                   ('auth', 'token'): 'tok\n', ('pr', 'diff', '9', '-R', 'o/r'): served}

        def fake(*args):
            if answers[args] is None:
                raise subprocess.CalledProcessError(1, ['gh', *args], stderr=refusal)
            return answers[args]
        return fake

    def setUp(self):  # every fetch also runs under ambient config and attributes that would hide the added lines
        self.enterContext(mock.patch.object(mod, 'REMOTE', self.remote))
        self.enterContext(mock.patch.dict(os.environ, {'GIT_CONFIG_PARAMETERS': "'diff.noprefix'='true'", 'XDG_CONFIG_HOME': self.root}))

    def test_a_refused_diff_is_the_merge_base_to_head_diff_with_the_heads_files(self):
        head, diff_text, contents = mod.read_diff(self.gh(), 'o/r', 9)
        self.assertEqual([(p, n, k, t) for p, n, k, t in mod.diff_rows(diff_text) if k in '+!'],
                         [('a.py', 2, '+', 'b'), ('b.py', None, '!', ''), ('new.py', 1, '+', 'n'), ('té.py', 1, '+', 'x = 1\rassert v')])
        self.assertEqual((head, contents), (self.head, {'a.py': 'a\nb\n', 'b.py': 'b\0\n', 'moved.py': 'g\n', 'new.py': 'n\n',
                                                        'té.py': 'x = 1\rassert v\n'}))

    def test_a_served_diff_is_kept_and_any_other_refusal_raised_without_a_fetch(self):
        self.assertEqual(mod.read_diff(self.gh(served='diff'), 'o/r', 9), (self.head, 'diff', {}))
        self.assertRaisesRegex(subprocess.CalledProcessError, "'pr', 'diff'", mod.read_diff, self.gh(refusal='HTTP 502: Bad Gateway'), 'o/r', 9)

    def test_blobs_are_read_in_one_fetch_refused_whole_and_none_fetches_nothing(self):
        self.assertEqual(mod.read_blobs(self.gh(), 'o/r', self.blobs), dict(zip(self.blobs, ('a\nb\n', 'x = 1\rassert v\n'))))
        self.assertRaisesRegex(subprocess.CalledProcessError, "'fetch'", mod.read_blobs, self.gh(), 'o/r', [self.blobs[0], '0' * 40])
        self.assertEqual(mod.read_blobs(None, 'o/r', []), {})

    def test_a_failed_fetch_is_raised_never_an_empty_diff(self):
        self.assertRaisesRegex(subprocess.CalledProcessError, "'fetch'", mod.read_diff, self.gh(merge_base='0' * 40), 'o/r', 9)


class DiffRows(unittest.TestCase):
    def test_a_row_ends_at_a_newline_alone(self):
        for end in '\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029':  # every other line end str.splitlines() honours
            with self.subTest(end=repr(end)):
                self.assertEqual(list(mod.diff_rows(f'+++ b/t.py\r\n@@ -0,0 +1,2 @@\r\n+x = 1{end}assert v\r\n+y\n')),
                                 [('t.py', 1, '@', ''), ('t.py', 1, '+', f'x = 1{end}assert v'), ('t.py', 2, '+', 'y')])

    def test_a_row_is_a_header_only_past_the_rows_its_hunk_counts(self):
        text = 'diff --git a/t.py b/t.py\n--- a/t.py\n+++ b/t.py\n@@ -1,2 +1,2 @@\n+++ b\n--- a\n+++ c\n--- d\n--- a/v\n+++ b/v\n@@ -1 +1 @@\n--- q\n+++ w\n'
        self.assertEqual(list(mod.diff_rows(text)), [('t.py', 1, '@', ''), ('t.py', 1, '+', '++ b'), ('t.py', None, '-', '-- a'),
                                                     ('t.py', 2, '+', '++ c'), ('t.py', None, '-', '-- d'), ('v', 1, '@', ''), ('v', None, '-', '-- q'), ('v', 1, '+', '++ w')])

    def test_a_quoted_or_tabbed_name_is_read_as_named(self):
        for target, path in (('"b/\\a\\b\\t\\n\\v\\f\\r\\"\\\\\\303\\251.py"', '\a\b\t\n\v\f\r"\\é.py'),
                             ('b/a b.py\t', 'a b.py'), ('"b/t \\303\\251.py"\t', 't é.py')):
            with self.subTest(target=target):
                self.assertEqual(list(mod.diff_rows(f'+++ {target}\n@@ -0,0 +1 @@\n+x\n'))[1], (path, 1, '+', 'x'))

    def test_a_binary_file_is_marked_unless_deleted(self):
        text = ('diff --git a/o b/n\nrename from o\nrename to "n\\303\\251"\nBinary files a/o and "b/n\\303\\251" differ\ndiff --git a/t1 b/t1\nBinary files /dev/null and b/t1 differ\n'
                'diff --git a/t2 and b/x b/t2 and b/x\nBinary files a/t2 and b/x and b/t2 and b/x differ\ndiff --git a/t3 b/t3\nBinary files a/t3 and /dev/null differ\n')
        self.assertEqual([p for p, _, k, _ in mod.diff_rows(text) if k == '!'], ['né', 't1', 't2 and b/x'])
        self.assertEqual({k for _, _, k, _ in mod.diff_rows(text)}, {'!'})


class Output(unittest.TestCase):
    def test_the_gh_seam_keeps_stdout_line_ends_and_a_failures_stderr(self):  # output() as check_pin_tests._gh runs gh
        with tempfile.TemporaryDirectory() as bin_dir:
            pathlib.Path(bin_dir, 'gh').write_text('#!/bin/sh\nprintf "a\\rb\\r\\n"; printf "c\\rd" >&2\n[ "$1" = kill ] && kill -9 $$; exit "$1"\n', encoding='utf-8')
            os.chmod(os.path.join(bin_dir, 'gh'), 0o700)
            with mock.patch.dict(os.environ, {'PATH': bin_dir + os.pathsep + os.environ['PATH']}):
                self.assertEqual(screen._gh('0'), 'a\rb\r\n')
                for arg, code in (('3', 3), ('kill', -9)):  # an exit status, then a signal
                    with self.assertRaises(subprocess.CalledProcessError) as raised:
                        screen._gh(arg)
                    self.assertEqual((raised.exception.returncode, raised.exception.stdout, raised.exception.stderr), (code, 'a\rb\r\n', 'c\rd'))
