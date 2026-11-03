"""The GitLab CI pin source: what `.gitlab-ci.yml` and its local includes pin, and the tests listed for it."""
import importlib.util
import os
import unittest

import test_check_pin_tests as base

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location('check_pin_tests', os.path.join(KIT, 'check_pin_tests.py'))
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

CI = '''\
include:
  - local: '/ci/lint.yml'
  - 'templates/build.yml'
  - {local: ci/flow.yml}
  - project: 'group/shared'
    file:
      - '/shared/a.yml'
  - remote: 'https://example.com/r.yml'
  - template: Auto-DevOps.gitlab-ci.yml
  - https://example.com/plain.yml
image: python:3.11-slim  # the default
variables:
  name: not-an-image:1.0
services:
  - postgres:15.4
  - name: redis:7.2.4
    alias: cache
    command: ["redis-server", "--x:9.9"]
  - {name: "mysql:8.0", alias: db}
default:
  image:
    name: node:20.11-alpine
    entrypoint: [""]
test:
  image: "$CI_REGISTRY/app:$TAG"
  services: [docker:24.0-dind, {name: 'memcached:1.6'}]
  before_script:
    - pip install ruff==0.6.9
  script:
    - npm i -g pnpm@9.1.0
  after_script: echo done==1.0.0
  environment:
    name: production:1.2
child:
  trigger:
    include:
      - local: child/pipeline.yml
deploy:
  image: alpine@sha256:''' + 'ab' * 32 + '''
  services:
  - busybox:1.36
  script: echo hi
'''


class GitlabCiPins(unittest.TestCase):
    """manifest_pins and gitlab_ci over each production the reader admits."""

    def test_each_source_path_has_its_kind(self):
        table = {'.gitlab-ci.yml': 'gitlab-ci', '.gitlab-ci.yaml': 'gitlab-ci', 'ci/.gitlab-ci.yml': None,
                 '.gitlab-ci.yml.bak': None, '.gitlab/ci/jobs.yml': None}
        for path, kind in table.items():
            with self.subTest(path=path):
                self.assertEqual(mod.source_kind(path), kind)

    def test_every_image_and_installer_form_is_read(self):
        self.assertEqual(mod.manifest_pins('gitlab-ci', CI), {
            'python:3.11-slim', '3.11-slim', 'postgres:15.4', '15.4', 'redis:7.2.4', '7.2.4', 'mysql:8.0', '8.0',
            'node:20.11-alpine', '20.11-alpine', 'docker:24.0-dind', '24.0-dind', 'memcached:1.6', '1.6',
            'ab' * 32, 'busybox:1.36', '1.36', '0.6.9', '9.1.0'})

    def test_a_name_or_list_item_outside_an_image_pins_nothing(self):
        table = {'variables:\n  name: foo:1.2\n': set(), 'job:\n  environment:\n    name: prod:1.2\n': set(),
                 'services:\n  - name: redis:7.2\n    command:\n      - "x:9.9"\n': {'redis:7.2', '7.2'},
                 'job:\n  script:\n    - echo redis:7.2\n': set(), 'image: *base\n': set(),
                 'image: python:${PY}\n': set(), 'image: $REGISTRY/python:3.11\n': set(),
                 "services: [{name: redis:7.2, alias: 'cache:9.9'}]\n": {'redis:7.2', '7.2'},
                 "services: [{name: redis:7.2, command: ['--port:9.9']}]\n": {'redis:7.2', '7.2'},
                 'job:\n  script:\n    - |\n      image: fake:9.9\n  image: alpine:3.19\n': {'alpine:3.19', '3.19'},
                 'image: &base python:3.11\n': {'python:3.11', '3.11'},
                 'image: &base\n  name: python:3.11\n': {'python:3.11', '3.11'},
                 'image:\n  name: node:20.1\n  entrypoint:\n  - "x:9.9"\n': {'node:20.1', '20.1'},
                 'services:\n  - name: redis:7.2\n    variables:\n      name: x:9.9\n': {'redis:7.2', '7.2'},
                 'services:\n  - command: |\n      run x:9.9\n    name: redis:7.2\n': {'redis:7.2', '7.2'}}
        for text, pins in table.items():
            with self.subTest(text=text):
                self.assertEqual(mod.manifest_pins('gitlab-ci', text), pins)

    def test_only_local_includes_are_followed(self):
        self.assertEqual(mod.gitlab_ci(CI)[1],
                         ['ci/lint.yml', 'templates/build.yml', 'ci/flow.yml', 'child/pipeline.yml'])
        self.assertEqual(mod.gitlab_ci("include: '/a.yml'\n")[1], ['a.yml'])
        self.assertEqual(mod.gitlab_ci('include:\n  local: b.yml\n')[1], ['b.yml'])
        self.assertEqual(mod.gitlab_ci("include: ['c.yml', {remote: 'https://x/d.yml'}]\n")[1], ['c.yml'])
        for text in ("include: [{project: 'g/x', file: 'e.yml'}]\n", "include: [{project: 'g/x', file: ['e.yml']}]\n",
                     "include:\n  - local: 'ci/$CI_COMMIT_REF_NAME.yml'\n", 'include: *shared\n',
                     'gen:\n  script: |\n    include: child.yml\n'):
            with self.subTest(text=text):
                self.assertEqual(mod.gitlab_ci(text)[1], [])
        self.assertEqual(mod.gitlab_ci("include: [{local: f.yml, rules: [{if: '$X'}]}]\n")[1], ['f.yml'])
        self.assertEqual(mod.gitlab_ci('include:\n  - local: a.yml\n    inputs:\n      local: b.yml\n')[1], ['a.yml'])
        self.assertEqual(mod.gitlab_ci("include: ['ci/a,b.yml', \"c.yml\"]\n")[1], ['ci/a,b.yml', 'c.yml'])


class GitlabCiFindings(unittest.TestCase):
    """The issue's reproduction: a test reading .gitlab-ci.yml and holding one of its pins is listed."""

    TEST = ("TEXT = Path('.gitlab-ci.yml').read_text()",
            "assert 'image: python:3.11-slim' in TEXT",
            "assert 'ruff==0.6.9' in TEXT",
            "assert 'image: python:' in TEXT")

    def test_the_issues_reproduction_is_listed(self):
        text = base.diff('.gitlab-ci.yml', 'image: python:3.11-slim', 'script:', '  - pip install ruff==0.6.9')
        text += base.diff('tests/test_ci.py', *self.TEST)
        found = mod.findings(text, (), None, None, lambda path, kind: mod.couples('\n'.join(self.TEST), kind))
        self.assertEqual([(p, n) for p, n, _ in found], [('tests/test_ci.py', 2), ('tests/test_ci.py', 3)])

    def test_a_test_that_reads_no_gitlab_ci_file_is_not_listed(self):
        literals = {'gitlab-ci': {'python:3.11-slim', '3.11-slim'}}
        other = ("TEXT = Path('README.md').read_text()", "assert 'python:3.11-slim' in TEXT")
        found = mod.findings(base.diff('tests/test_ci.py', *other), (), None, literals,
                             lambda path, kind: mod.couples('\n'.join(other), kind))
        self.assertEqual(found, [])

    def test_a_block_open_at_one_hunks_end_does_not_reach_the_next(self):
        text = (base.diff('.gitlab-ci.yml', 'services:', '  - postgres:15.4')
                + '@@ -40,1 +40,1 @@\n+    name: review:2.0\n'
                + base.diff('tests/test_ci.py', "TEXT = Path('.gitlab-ci.yml').read_text()", "assert 'review:2.0' in TEXT"))
        found = mod.findings(text, (), None, None, lambda path, kind: kind == 'gitlab-ci')
        self.assertEqual(found, [])

    def test_couples_reads_the_gitlab_ci_file_by_name(self):
        self.assertTrue(mod.couples("Path('.gitlab-ci.yaml').read_text()", 'gitlab-ci'))
        self.assertFalse(mod.couples("NAME = '.gitlab-ci.yml'", 'gitlab-ci'))
        self.assertTrue(mod.couples("open('ci/jobs.yml').read()", 'gitlab-ci', ('ci/jobs.yml',)))
        self.assertFalse(mod.couples("open('ci/other.yml').read()", 'gitlab-ci', ('ci/jobs.yml',)))
        self.assertFalse(mod.couples("open('data.yml').read()", 'gitlab-ci', ('a.yml',)))


class GitlabCiMain(unittest.TestCase):
    """main() end to end: the head's .gitlab-ci.yml and every local file it includes are read."""

    run_main = base.Main.run_main

    ROOT = "include:\n  - local: ci/jobs.yml\n  - 'ci/*.yml'\nimage: python:3.11-slim\n"
    JOBS = 'include: ci/deep/more.yml\ntest:\n  image: node:20.11-alpine\n'
    MORE = 'include: [ci/jobs.yml, .gitlab-ci.yml]\nlint:\n  services: [postgres:15.4]\n'  # a cycle ends

    def test_a_pin_in_an_included_file_is_listed_for_a_test_reading_either_file(self):
        reads_root = "CI = Path('.gitlab-ci.yml').read_text()\n"
        reads_jobs = "CI = Path('ci/jobs.yml').read_text()\n"
        diff_text = (base.diff('tests/test_root.py', "assert 'postgres:15.4' in CI")
                     + base.diff('tests/test_jobs.py', "assert 'node:20.11-alpine' in CI"))
        code, out, _, _ = self.run_main(
            diff_text, {'.gitlab-ci.yml': self.ROOT, 'ci/jobs.yml': self.JOBS, 'ci/deep/more.yml': self.MORE},
            {'tests/test_root.py': reads_root, 'tests/test_jobs.py': reads_jobs})
        self.assertEqual((code, out), (1, ["tests/test_root.py:1: assert 'postgres:15.4' in CI",
                                           "tests/test_jobs.py:1: assert 'node:20.11-alpine' in CI"]))

    def test_an_include_missing_from_the_tree_is_named(self):
        code, out, err, _ = self.run_main(base.diff('tests/test_x.py', 'x = 1'),
                                          {'.gitlab-ci.yml': 'include: ci/gone.yml\n', 'ci/gonexyml': 'x: 1\n'}, {'tests/test_x.py': 'x = 1\n'})
        self.assertEqual((code, out), (2, []))
        self.assertEqual(err[0], 'could not screen ci/gone.yml: included by .gitlab-ci.yml, not in the tree')

    def test_a_wildcard_include_reaches_only_the_depth_it_names(self):
        diff_text = base.diff('tests/test_ci.py', "assert 'redis:7.2' in CI", "assert 'mysql:8.0' in CI")
        for root, listed in (("include: 'ci/*.yml'\n", ["tests/test_ci.py:1: assert 'redis:7.2' in CI"]),
                             ("include: 'ci/**.yml'\n", ["tests/test_ci.py:1: assert 'redis:7.2' in CI",
                                                         "tests/test_ci.py:2: assert 'mysql:8.0' in CI"]),
                             ("include: 'ci/**/*.yml'\n", ["tests/test_ci.py:1: assert 'redis:7.2' in CI",
                                                           "tests/test_ci.py:2: assert 'mysql:8.0' in CI"])):
            with self.subTest(root=root):
                code, out, _, _ = self.run_main(
                    diff_text, {'.gitlab-ci.yml': root, 'ci/a.yml': 'image: redis:7.2\n',
                                'ci/deep/b.yml': 'image: mysql:8.0\n'},
                    {'tests/test_ci.py': "CI = Path('.gitlab-ci.yml').read_text()\n"})
                self.assertEqual((code, out), (1, listed))

    def test_a_run_of_stars_is_one_wildcard(self):
        code, out, _, _ = self.run_main(base.diff('tests/test_ci.py', "assert 'redis:7.2' in CI"),
                                        {'.gitlab-ci.yml': "include: '" + '*' * 40 + "x.yml'\n", 'a/b/x.yml': 'image: redis:7.2\n'},
                                        {'tests/test_ci.py': "CI = Path('.gitlab-ci.yml').read_text()\n"})
        self.assertEqual((code, out), (1, ["tests/test_ci.py:1: assert 'redis:7.2' in CI"]))

    def test_an_include_with_too_many_wildcards_is_named_not_matched(self):
        code, out, err, _ = self.run_main(base.diff('tests/test_x.py', 'x = 1'),
                                          {'.gitlab-ci.yml': "include: '*a*b*c*d*e.yml'\n"}, {'tests/test_x.py': 'x = 1\n'})
        self.assertEqual((code, out), (2, []))
        self.assertEqual(err[0], 'could not screen *a*b*c*d*e.yml: included by .gitlab-ci.yml, with more than 4 wildcards')
