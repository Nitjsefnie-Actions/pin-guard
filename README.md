# pin-guard

Screens a pull request's test files for assertions that pin a dependency, and
reports the candidates as review line comments plus one sticky status comment
per screened head. The rule this action screens for: **no test asserts on a
pinned version, tag, SHA or digest of any dependency** — a workflow `uses:`
value (the action's SHA, release, version comment or pin shape) or its
`with:` inputs, a Dockerfile `FROM` base image, a tool version or a package
version. A legitimate bump must never fail a test.

The action reads the pull request only through the GitHub API at the head
being screened; it never checks out or executes pull-request code.

## Install

Save this workflow as `.github/workflows/pin-guard.yml`. The action reference
is consumed by commit SHA — a tag is a moving pointer — with the release
version kept in a comment beside it.

```yaml
name: pin test screen
on: # zizmor: ignore[dangerous-triggers]
  pull_request_target:
    types: [opened, synchronize, reopened, ready_for_review]
permissions:
  contents: read
  pull-requests: write
concurrency:
  group: pin-guard-${{ github.event.pull_request.number }}
  cancel-in-progress: true
jobs:
  pin-guard:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: Nitjsefnie-Actions/pin-guard@6cb3cc58e29666f593d6ad7a582095d9039e1692 # v1.0.2
        with:
          fail-on-candidates: 'false'
          github-token: ${{ github.token }}
```

The `with:` block shows **every input with its default**. Omit it and a bare
`uses:` step behaves the same. `github-token` is the token for the API reads
and the comment writes. The action recognizes its own earlier comments by
the login the token posts as, so the token needs only the workflow's
`pull-requests: write` permission. Keep the default `github.token`: it posts
as `github-actions[bot]`, and the action knows that identity outright — a
GitHub App installation token cannot be read a login from at all (GitHub
answers 403 for `GET /user` on installation tokens). A personal access token
works too: the action reads its login once per run and recognizes its
comments by it; a token that is neither makes the run refuse before any
screen, rather than post comments it would never find again.

The `permissions:` block is least-privilege: `contents: read` reads the pull
request's files at the head, and `pull-requests: write` posts the review and
the status comment. There is no checkout step — nothing from a pull request's
tree is fetched into the workspace, let alone executed.

The per-pull-request `concurrency:` group cancels the run a newer head
supersedes, so only the newest head's screen reports.

`pull_request_target` runs this workflow — its steps come from the
repository's own default branch — for a fork's pull request too, which is
what screens a fork PR. zizmor's `dangerous-triggers` audit reports that
trigger; this job is safe under it because it never checks out or executes
pull-request code, and the inline `# zizmor: ignore[dangerous-triggers]`
comment records that judgment beside the trigger, so a repository copying
the block keeps a green zizmor gate on the upgrade.

## What the action reports

For each screened head with candidates the action submits one review with
event `COMMENT` — never `REQUEST_CHANGES` or `APPROVE` — whose `commit_id`
is the screened head SHA, carrying one inline comment per candidate anchored
on that line of the pull request's diff, so every finding is its own
resolvable thread (split into several reviews on that head only when GitHub
refuses one review's size). A candidate on a line outside the diff is listed in the
review body, because GitHub accepts inline comments only on diff lines. Each
inline comment shows the line's text in a code fence and the guidance below.
The action never resolves or dismisses a thread itself.

A completed screen writes one sticky status comment per pull request, never
duplicated: an earlier status comment is edited in place on every later head,
and with no earlier one a clean head posts nothing — a head with candidates,
or one that could not be screened, always gets one. It names the screened
head SHA, the candidate count and each candidate as `path:line`.

## Not a gate by default

`fail-on-candidates` (default `'false'`) makes the job fail when the screened
head has candidates. A screen that cannot run fails the job either way, and
the status comment says so: an unreadable pull request, an unparseable pin
source or test file, a timeout, or a crash — every outcome other than a
completed screen counts as could-not-screen. A run that screened nothing
never reports clean.

The exit code follows the same rule: 0 reports a completed screen — clean,
or candidates with `fail-on-candidates` off; 1 reports candidates with it
on, a screen that could not run, a status comment cut by GitHub's comment
length limit, or a failed delivery — never the bare presence of candidates.

## A listing, never a verdict

The screen lists candidates; it is not a verdict. It misses pins it cannot
read as literals from a source the test reads, so an empty list is not a
proof. A reader judges each candidate:

- If the line pins a dependency, delete it, then resolve the thread.
- Otherwise, resolve the thread with a reply naming why the line is a
  consistency check between files or steps, or a security invariant
  enumerated over every workflow.

## The status comment's hidden block

Every status comment ends with a marker line and a hidden machine-readable
block:

```
<!-- pin-guard: status -->
<!-- {"head_sha": "...", "status": "candidates", "count": 2,
      "candidates": [["tests/test_x.py", 12, "..."]], "reason": "..."} -->
```

The block is JSON: every `<`, `>` and `-` in it is written as its JSON
unicode escape (`\u003c`, `\u003e`, `\u002d`), so a parser reads it
directly and a candidate's text can never close the comment or leak
markup. The `status` field is `clean`, `candidates` or
`could-not-screen`; a run that screened nothing never parses as clean.
`reason` names why the head is not clean — a screen that could not run or
a review that could not be delivered — and is absent on a clean head. The
block always carries the full candidate list, even when GitHub's comment
length limit cut the visible list to what fits with the omission count
stated. When even the full hidden list cannot fit, the block instead
carries `{"head_sha": ..., "status": ..., "count": N, "truncated": true}`
with no `candidates` key. A consumer reads the block after the LAST
`<!-- pin-guard: status -->` marker line in the comment: the visible text
above the marker is not machine-readable, and a planted marker line inside
rendered error text cannot fool a reader of the last block. This
repository only writes the block; reading it before a merge is the
reader's job.

Trust only the status comment whose author is the login the workflow's
configured `github-token` posts as — the action recognizes its own comments
only among comments authored by that login carrying the marker on a line of
its own — and whose `head_sha` equals the head being merged.

## License

Licensed under the [MIT License](LICENSE).
