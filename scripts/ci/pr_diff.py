"""read_diff(gh, repo, pr): the PR's head and diff, plus the head's changed files when git made the diff.

On GitHub's HTTP 406 (a diff past its limits, such as 300 files) git diffs the merge base and head.
"""
import base64
import json
import os
import re
import subprocess
import tempfile

REMOTE = 'https://github.com/{}.git'
_HUNK = re.compile(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@')
_ESCAPES = dict(zip(b'abtnvfr"\\', b'\a\b\t\n\v\f\r"\\'))


def read_diff(gh, repo, pr) -> tuple[str, str, dict]:
    head = json.loads(gh('pr', 'view', str(pr), '-R', repo, '--json', 'headRefOid'))['headRefOid']
    try:
        return head, gh('pr', 'diff', str(pr), '-R', repo), {}
    except subprocess.CalledProcessError as exc:
        if 'HTTP 406' not in (exc.stderr or ''):
            raise
    base = json.loads(gh('pr', 'view', str(pr), '-R', repo, '--json', 'baseRefOid'))['baseRefOid']
    merge_base = json.loads(gh('api', f'repos/{repo}/compare/{base}...{head}?per_page=1'))['merge_base_commit']['sha']
    auth = base64.b64encode(f'x-access-token:{gh("auth", "token").strip()}'.encode()).decode()
    # No ambient GIT_* variable, config or attributes file shapes the diff; the token rides in env, never argv.
    env = {name: value for name, value in os.environ.items() if not name.startswith('GIT_')}
    env.update({'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_ATTR_NOSYSTEM': '1',
                'GIT_TERMINAL_PROMPT': '0', 'GIT_CONFIG_COUNT': '3', 'GIT_CONFIG_KEY_0': 'http.extraHeader',
                'GIT_CONFIG_VALUE_0': f'Authorization: basic {auth}', 'GIT_CONFIG_KEY_1': 'maintenance.auto',
                'GIT_CONFIG_VALUE_1': 'false', 'GIT_CONFIG_KEY_2': 'core.attributesFile', 'GIT_CONFIG_VALUE_2': os.devnull})
    with tempfile.TemporaryDirectory() as bare:
        def git(*args):
            return output(['git', '-C', bare, *args], env)
        git('init', '-q', '--bare', '--template=')
        git('fetch', '-q', '--no-tags', '--depth=1', '--filter=blob:none', REMOTE.format(repo), merge_base, head)
        diff_text = git('diff', '--no-ext-diff', '--no-textconv', merge_base, head)
        raw = git('diff', '--raw', '-z', '--no-abbrev', '--no-renames', merge_base, head).split('\0')[:-1]
        blobs = {path: meta.split()[3] for meta, path in zip(raw[::2], raw[1::2])
                 if meta.split()[1] in ('100644', '100755')}
        return head, diff_text, {path: git('cat-file', 'blob', sha) for path, sha in blobs.items()}


def output(argv, env=None):
    """argv's stdout, line ends as written: text mode reads a lone \\r as one."""
    done = subprocess.run(argv, capture_output=True, check=False, env=env)
    out, err = (text.decode('utf-8', 'replace') for text in (done.stdout, done.stderr))
    if done.returncode:
        raise subprocess.CalledProcessError(done.returncode, argv, out, err)
    return out


def _path(name):
    """A header's path: git tabs a name holding a space and C-quotes an unusual one."""
    name = name.removesuffix('\t')
    if len(name) < 2 or name[0] != '"' or name[-1] != '"':
        return name
    return re.sub(rb'\\([0-7]{3}|.)', lambda m: bytes([int(m[1], 8) if len(m[1]) == 3 else _ESCAPES[m[1][0]]]),
                  name[1:-1].encode()).decode('utf-8', 'replace')


def diff_rows(diff_text):
    """Yield hunk rows as (path, line, kind, text); '@' marks a hunk, '!' a binary file. Rows end at \\n alone."""
    path, line, renamed = None, 0, None
    for raw in re.split(r'\r?\n', diff_text):
        if raw.startswith('+++ '):
            target = _path(raw[4:])
            path = target[2:] if target.startswith('b/') else None
            continue
        if raw.startswith('diff --git'):
            renamed = None
        if raw.startswith('rename to '):
            renamed = _path(raw[len('rename to '):])
        if raw.startswith('Binary files ') and not raw.endswith(' and /dev/null differ'):
            sides = raw[len('Binary files '):-len(' differ')]
            new = sides[len('/dev/null and '):] if sides.startswith('/dev/null and ') else sides[(len(sides) + 5) // 2:]
            yield renamed or _path(new)[2:], None, '!', ''
        if raw.startswith('--- ') or raw.startswith('diff --git'):
            continue
        hunk = _HUNK.match(raw)
        if hunk:
            line = int(hunk.group(1))
            if path is not None:
                yield path, line, '@', ''
            continue
        if path is None or not raw or raw[0] not in '+- ':
            continue
        kind, text = raw[0], raw[1:]
        if kind == '-':
            yield path, None, kind, text
            continue
        yield path, line, kind, text
        line += 1
