#!/usr/bin/env python3
"""The sticky per-head status comment: found, rendered, and upserted in place.

    found = find_status(gh, repo, pr, bot_login)
    body, truncated = status_body(head_sha, candidates, clean=..., error=...)
    posted, url, truncated = upsert_status(gh, repo, pr, head_sha, candidates, clean=..., error=...)

Every function takes a `gh` callable `gh(*args, stdin=None) -> str` as its
first parameter — the only GitHub seam; tests inject a double that refuses
any call it does not model and records the calls it does. `stdin` carries a
write's JSON payload. The action's identity is `bot_login`, the login the
run's token posts as; recognition is the review marker's rule — the login
compared plus `STATUS_MARKER` on a line of its own.

The rendered body ends with the marker line and a hidden machine-readable
block:

    <!-- pin-guard: status -->
    <!-- {"head_sha": ..., "status": "clean" | "candidates" | "could-not-screen",
          "count": N, "candidates": [[path, line, text], ...], "reason": ...} -->

The block always carries the FULL candidate list and the screen's outcome
(`status`, with `reason` beside it whenever the head is not clean — a
delivery failure names itself there). Every `<`, `>` and `-` in the block
is written as its JSON unicode escape (`\\u003c`, `\\u003e`, `\\u002d`), so
the block is plain JSON a parser reads directly and can never close its
own comment or leak markup; `parse_block` reads it back the way a consumer
does. GitHub's comment limit is 65536 characters: when the body would
exceed it, the visible list shows what fits followed by how many were
omitted, the visible and hidden text still carry the full count, and the
boolean result is True — the caller fails the job. If even the full hidden
list cannot fit, the block carries `{"head_sha": ..., "status": ...,
"count": N, "truncated": true}` (the head SHA stays in the block: a
consumer matching the block's head against the head being merged needs it
there too) and the boolean is True.
"""
import json
import re

STATUS_MARKER = '<!-- pin-guard: status -->'
LIMIT = 65536
_BLOCK_PREFIX = '<!-- '
_BLOCK_SUFFIX = ' -->'

_JSON_ESCAPES = (('<', '\\u003c'), ('>', '\\u003e'), ('-', '\\u002d'))


def _escape(text):
    """The block's JSON with `<`, `>` and `-` forced to unicode escapes."""
    for char, escape in _JSON_ESCAPES:
        text = text.replace(char, escape)
    return text


def inert(text):
    """The text in an inline code span no run of its own backticks can close.

    Inside a code span GitHub renders `@mentions`, `#N` references, `-->`
    and raw HTML literally, so a filename or error line is safe to show;
    the span's backtick run is one longer than the longest run in the text,
    and one space pads each side — a text that STARTS with a backtick would
    otherwise merge with the opening fence into a run nothing closes, while
    CommonMark strips the padding spaces.
    """
    longest = max((len(run) for run in re.findall(r'`+', text)), default=0)
    fence = '`' * max(longest + 1, 1)
    return f'{fence} {text} {fence}'


def inert_block(text):
    """The text in a fenced block sized so no line of the text can close it.

    The same discipline as `inert`, for text that may hold newlines.
    """
    longest = max((len(run) for run in re.findall(r'`+', text)), default=0)
    fence = '`' * max(longest + 1, 3)
    return f'{fence}\n{text}\n{fence}'


def _own_lines(body):
    return (line.rstrip('\r') for line in (body or '').splitlines())


def find_status(gh, repo, pr, bot_login):
    """The action's own sticky status comment, or None.

    Reads `repos/{repo}/issues/{pr}/comments` with `--paginate --slurp`
    (every page's array in one array of pages, flattened here) and returns
    the one comment authored by `bot_login` whose body carries
    `STATUS_MARKER` on a line of its own.
    """
    comments = [item for page in json.loads(gh('api', f'repos/{repo}/issues/{pr}/comments', '--paginate', '--slurp'))
                for item in page]
    for item in comments:
        if (item.get('user') or {}).get('login') == bot_login \
                and STATUS_MARKER in _own_lines(item.get('body')):
            return item
    return None


def parse_block(body):
    """The hidden block's parsed JSON, read the way a consumer reads it.

    This reader exists for this module's own round-trip tests (and any
    in-repo use); consumers outside the repository are still told to read
    the block themselves — this repository only writes it.

    Finds the block after `STATUS_MARKER`'s line, parses the JSON — the
    `\\u003c`-style unicode escapes are plain JSON — and returns the dict:
    `{"head_sha", "status", "count", "candidates", "reason"?}`, or with
    `"truncated": true` instead of `candidates` when the full list could
    not fit. Raises AssertionError when no block follows the marker line.
    """
    after = body.rpartition(f'\n{STATUS_MARKER}\n')[2]
    _, separator, tail = after.partition(_BLOCK_PREFIX)
    if not separator or not tail.endswith(_BLOCK_SUFFIX):
        raise AssertionError(f'no hidden block follows the status marker: {after!r}')
    return json.loads(tail[:-len(_BLOCK_SUFFIX)])


def status_body(head_sha, candidates, *, clean=False, error=None, outcome=None):
    """The rendered status comment: `(body, truncated)`.

    The visible part names the screened head SHA, the candidate count, and
    each candidate as an inert `path:line`. A non-clean status renders the
    candidate list in every case; `error` adds the could-not-run sentence
    with its text held inert — the list is never dropped for it. The hidden
    block always carries the FULL candidate list plus the screen's outcome
    (the `status` field, with `reason` beside it whenever the head is not
    clean); `outcome` names it explicitly — 'clean' | 'candidates' |
    'could-not-screen' — and defaults to what `clean` and `error` imply.
    When the body would exceed `LIMIT`, the visible list is cut to what
    fits with the omission count stated and the boolean is True; when even
    the full hidden list cannot fit, the block states the count and
    `truncated: true`.
    """
    count = len(candidates)
    if outcome is None:
        outcome = 'clean' if clean else ('could-not-screen' if error is not None else 'candidates')
    rows = [[path, line, text] for path, line, text in candidates]
    block = {'head_sha': head_sha, 'status': outcome, 'count': count, 'candidates': rows}
    if not clean and error is not None:
        block['reason'] = error
    full = _escape(json.dumps(block, ensure_ascii=True))
    short = _escape(json.dumps({'head_sha': head_sha, 'status': outcome, 'count': count,
                                'truncated': True}, ensure_ascii=True))

    def assemble(shown_count, hidden):
        lines = [f'Pin-test screen of head {head_sha}: {count} candidate line(s).']
        if clean:
            lines.append('This head is clean: no candidate test lines.')
        else:
            lines.extend(f'- {inert(f"{path}:{line}")}' for path, line, _ in rows[:shown_count])
            omitted = count - shown_count
            if omitted:
                lines.append(f'{omitted} more candidate line(s) omitted from this comment.')
            if error is not None:
                lines.append('The screen could not run:')
                lines.append(inert_block(error))
        lines.extend((STATUS_MARKER, f'{_BLOCK_PREFIX}{hidden}{_BLOCK_SUFFIX}'))
        return '\n'.join(lines)

    if len(assemble(count, full)) <= LIMIT:
        return assemble(count, full), False
    for shown_count in range(count, -1, -1):
        if len(assemble(shown_count, full)) <= LIMIT:
            return assemble(shown_count, full), True
    for shown_count in range(count, -1, -1):
        if len(assemble(shown_count, short)) <= LIMIT:
            return assemble(shown_count, short), True
    return assemble(0, short), True


def upsert_status(gh, repo, pr, head_sha, candidates, *, bot_login, clean=False, error=None, outcome=None):
    """Post or edit the status comment; `(posted_or_edited, url, truncated)`.

    Finds the action's earlier status comment (`find_status` against
    `bot_login`, the login the run's token posts as) and edits it in place,
    or creates one; a clean head with no earlier status comment posts
    nothing and returns `(False, '', False)`. `outcome` names the screen's
    outcome for the hidden block and defaults to what `clean` and `error`
    imply. The truncated boolean is `status_body`'s.
    """
    existing = find_status(gh, repo, pr, bot_login)
    if clean and existing is None:
        return False, '', False
    body, truncated = status_body(head_sha, candidates, clean=clean, error=error, outcome=outcome)
    # The nested per-issue update path is the one GitHub 404s.
    endpoint = (f'repos/{repo}/issues/comments/{existing["id"]}' if existing is not None
                else f'repos/{repo}/issues/{pr}/comments')
    out = gh('api', endpoint, '--method', 'PATCH' if existing is not None else 'POST',
             '--input', '-', stdin=json.dumps({'body': body}, ensure_ascii=True))
    return True, json.loads(out)['html_url'], truncated
