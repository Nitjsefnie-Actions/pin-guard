# Contributing to pin-guard

Issues and pull requests are welcome, especially a test file the screen
misreads: a line it lists that pins nothing, or a pinned version, tag, SHA or
digest it misses. Include the file, the line and what the action reported.

## LLM and agent contributions are welcome

You may use an LLM or coding agent. There is no separate review queue or
expectation that you rewrite its output by hand. Two conditions apply:

1. Disclose the serving model in each commit it authored:

   ```
   Co-Authored-By: <Model Name> <noreply@example.com>
   ```

   Use the plain model name, without a context-window suffix. Inspect all
   outgoing commits, not only the tip:

   ```bash
   git log --format='%h %(trailers:key=Co-Authored-By,valueonly)' origin/main..HEAD
   ```

2. Verify every behavioral claim. Paste the command, actual output and exit
   status.

## A new file is invisible until you name it

`.gitignore` denies by default and names back the files this repository
ships. Reopen a directory before allowing files inside it:

```
*
!tests/
tests/*
!tests/test_check_pin_tests.py
```

After changing ignore rules, seed a junk file in each kept directory, prove
`git check-ignore -v` ignores it, and verify every intended file is tracked.

## Getting it running

Use Python 3.15. The screen and its driver use only the standard library;
the suite needs pytest, installed from the hash-pinned manifest:

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install --require-hashes --only-binary=:all: -r requirements-test.txt
python3 -m pytest tests/ -q --tb=short -ra
```

The [tests workflow](.github/workflows/tests.yml) also runs Ruff,
pycodestyle, pylint and pyright, and gates coverage on the floor recorded in
`.github/ci-thresholds.json`. When a green tests run on main measures
coverage above the recorded measurement by more than the hysteresis in
`.github/scripts/ratchet_thresholds.py`, the
[ratchet push workflow](.github/workflows/ratchet-push.yml) raises that floor
in an automated commit. The size records (`.github/size-baseline.json`)
ride the pull request that changes the tree: when a change tightens a
record, commit the tightened entry with it. The comment-share records
(`.github/comment-share.json`) are tightened on each push to main by the
[comment share workflow](.github/workflows/comment-share.yml), which
commits any changed entry itself.

## Commit messages

Every commit message follows Conventional Commits:

```
type(scope)!: subject
```

The type is one of `build`, `chore`, `ci`, `docs`, `feat`, `fix`, `perf`,
`refactor`, `revert`, `style`, `test`; the scope and `!` are optional. A
breaking change carries `!`. Review checks that each type and `!` match the
change, and a wrong message is reworded before the merge.

Messages automation writes follow it too: Dependabot's are
`ci(deps): bump …`, and the coverage ratchet's is
`chore(ratchet): raise the coverage floor (automated)`.

## Issues

Use the [issue form](.github/ISSUE_TEMPLATE/issue.md). Comment `/claim` on an
open unassigned issue before you start, then confirm your login appears in
its assignees.

## Pull requests

Keep changes small and single-purpose. Claim the issue first, name it in the
pull request's Related Issues and Pull Requests section, and follow the
[pull request template](.github/PULL_REQUEST_TEMPLATE.md). Author and commit
with an email linked to your GitHub account: the pull request gate checks that
every commit's author and committer resolve to you, or that the commit carries
a `Co-Authored-By:` trailer whose email does. File a second defect separately
rather than expanding the patch.

Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md).
