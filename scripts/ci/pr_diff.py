"""read_diff(gh, repo, pr): the PR's head and diff, plus the head's changed files when git made the diff.

On GitHub's HTTP 406 (a diff past its limits, such as 300 files) git diffs the merge base and head.
"""
import base64
import json
import os
import subprocess
import tempfile

REMOTE = 'https://github.com/{}.git'


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
    # No ambient GIT_* variable or config shapes the diff; the token rides in env, never argv.
    env = {name: value for name, value in os.environ.items() if not name.startswith('GIT_')}
    env.update({'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_TERMINAL_PROMPT': '0',
                'GIT_CONFIG_COUNT': '2', 'GIT_CONFIG_KEY_0': 'http.extraHeader',
                'GIT_CONFIG_VALUE_0': f'Authorization: basic {auth}', 'GIT_CONFIG_KEY_1': 'maintenance.auto',
                'GIT_CONFIG_VALUE_1': 'false'})
    with tempfile.TemporaryDirectory() as bare:
        def git(*args):
            return subprocess.run(['git', '-C', bare, *args], capture_output=True, text=True, encoding='utf-8',
                                  errors='replace', check=True, env=env).stdout
        git('init', '-q', '--bare')
        git('fetch', '-q', '--no-tags', '--depth=1', '--filter=blob:none', REMOTE.format(repo), merge_base, head)
        diff_text = git('diff', '--no-ext-diff', '--no-textconv', merge_base, head)
        raw = git('diff', '--raw', '-z', '--no-abbrev', '--no-renames', merge_base, head).split('\0')[:-1]
        blobs = {path: meta.split()[3] for meta, path in zip(raw[::2], raw[1::2])
                 if meta.split()[1] in ('100644', '100755')}
        return head, diff_text, {path: git('cat-file', 'blob', sha) for path, sha in blobs.items()}
