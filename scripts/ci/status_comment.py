#!/usr/bin/env python3
"""The sticky per-head status comment: found, rendered, and upserted in place.

    found = find_status(gh, repo, pr, bot_login)
    body, truncated = status_body(head_sha, candidates, clean=..., error=...)
    posted, url, truncated = upsert_status(gh, repo, pr, head_sha, candidates, clean=..., error=...)

Every function takes a `gh` callable `gh(*args, stdin=None) -> str` as its
first parameter — the only GitHub seam; tests inject a double that refuses
any call it does not model and records the calls it does. `stdin` carries a
write's JSON payload. The action's bot identity is `BOT_LOGIN`; recognition
is the same rule as the review marker's: the author login compared AND
`user.type == 'Bot'`, with `STATUS_MARKER` on a line of its own in the
body — marker text anywhere else never matches.

The rendered body ends with the marker line and a hidden machine-readable
block:

    <!-- pin-guard: status -->
    <!-- {"head_sha": ..., "count": N, "candidates": [[path, line, text], ...]} -->

The block always carries the FULL candidate list, with every `&`, `<`, `>`
and `-` escaped (`&amp;`, `&lt;`, `&gt;`, `&dash;`) so it can never close
its own comment or leak markup; `parse_block` reads it back the way a
consumer does. GitHub's comment limit is 65536 characters: when the body
would exceed it, the visible list shows what fits followed by how many were
omitted, the visible and hidden text still carry the full count, and the
boolean result is True — the caller fails the job. If even the full hidden
list cannot fit, the block carries `{"head_sha": ..., "count": N,
"truncated": true}` (the head SHA stays in the block: a consumer matching
the block's head against the head being merged needs it there too) and the
boolean is True.
"""
import json

STATUS_MARKER = '<!-- pin-guard: status -->'
LIMIT = 65536
BOT_LOGIN = 'github-actions[bot]'
_BLOCK_PREFIX = '<!-- '
_BLOCK_SUFFIX = ' -->'

_ESCAPE = (('&', '&amp;'), ('<', '&lt;'), ('>', '&gt;'), ('-', '&dash;'))


def _escape(text):
    """Escape a hidden block's markup: `&` first, so the rest is reversible."""
    for char, entity in _ESCAPE:
        text = text.replace(char, entity)
    return text


def _unescape(text):
    """The exact inverse of `_escape`, applied in reverse order."""
    for char, entity in reversed(_ESCAPE):
        text = text.replace(entity, char)
    return text


def _own_lines(body):
    return (line.rstrip('\r') for line in (body or '').splitlines())


def find_status(gh, repo, pr, bot_login):
    """The action's own sticky status comment, or None.

    Reads `repos/{repo}/issues/{pr}/comments` with `--paginate` and returns
    the one comment authored by `bot_login` (login AND `type == 'Bot'`)
    whose body carries `STATUS_MARKER` on a line of its own.
    """
    comments = json.loads(gh('api', f'repos/{repo}/issues/{pr}/comments', '--paginate'))
    for item in comments:
        user = item.get('user') or {}
        if user.get('login') == bot_login and user.get('type') == 'Bot' \
                and STATUS_MARKER in _own_lines(item.get('body')):
            return item
    return None


def parse_block(body):
    """The hidden block's parsed JSON, read the way a consumer reads it.

    Finds the block after `STATUS_MARKER`'s line, unescapes it, parses the
    JSON, and returns the dict — `{"head_sha", "count", "candidates"}`, or
    `{"head_sha", "count", "truncated": True}` when the full list could not
    fit. Raises AssertionError when no block follows the marker line.
    """
    after = body.rpartition(f'\n{STATUS_MARKER}\n')[2]
    head, separator, tail = after.partition(_BLOCK_PREFIX)
    if not separator or not tail.endswith(_BLOCK_SUFFIX):
        raise AssertionError(f'no hidden block follows the status marker: {after!r}')
    return json.loads(_unescape(tail[:-len(_BLOCK_SUFFIX)]))


def status_body(head_sha, candidates, *, clean=False, error=None):
    """The rendered status comment: `(body, truncated)`.

    The visible part names the screened head SHA, the candidate count, and
    each candidate as `path:line`; `clean=True` renders the clean sentence
    instead of a list (a clean head has no candidates), and `error` renders
    the could-not-run sentence. The hidden block always carries the FULL
    candidate list. When the body would exceed `LIMIT`, the visible list is
    cut to what fits with the omission count stated and the boolean is
    True; when even the full hidden list cannot fit, the block states the
    count and `truncated: true`.
    """
    count = len(candidates)
    rows = [[path, line, text] for path, line, text in candidates]
    full = _escape(json.dumps({'head_sha': head_sha, 'count': count, 'candidates': rows},
                              ensure_ascii=True))
    short = _escape(json.dumps({'head_sha': head_sha, 'count': count, 'truncated': True},
                               ensure_ascii=True))

    def assemble(shown_count, hidden):
        lines = [f'Pin-test screen of head {head_sha}: {count} candidate line(s).']
        if clean:
            lines.append('This head is clean: no candidate test lines.')
        elif error is not None:
            lines.append(f'The screen could not run: {error}')
        else:
            lines.extend(f'- {path}:{line}' for path, line, _ in rows[:shown_count])
            omitted = count - shown_count
            if omitted:
                lines.append(f'{omitted} more candidate line(s) omitted from this comment.')
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


def upsert_status(gh, repo, pr, head_sha, candidates, *, clean=False, error=None):
    """Post or edit the status comment; `(posted_or_edited, url, truncated)`.

    Finds the action's earlier status comment (`find_status` against
    `BOT_LOGIN`) and edits it in place, or creates one; a clean head with no
    earlier status comment posts nothing and returns `(False, '', False)`.
    The truncated boolean is `status_body`'s.
    """
    existing = find_status(gh, repo, pr, BOT_LOGIN)
    if clean and existing is None:
        return False, '', False
    body, truncated = status_body(head_sha, candidates, clean=clean, error=error)
    if existing is not None:
        endpoint = f'repos/{repo}/issues/{pr}/comments/{existing["id"]}'
    else:
        endpoint = f'repos/{repo}/issues/{pr}/comments'
    out = gh('api', endpoint, '--method', 'PATCH' if existing is not None else 'POST',
             '--input', '-', stdin=json.dumps({'body': body}, ensure_ascii=True))
    return True, json.loads(out)['html_url'], truncated
