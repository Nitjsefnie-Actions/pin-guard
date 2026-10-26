#!/usr/bin/env python3
"""The action's review line comments: found among a PR's comments, planned against a screened head, and posted.

    current = current_bot_comments(gh, repo, pr, head_sha, bot_login)
    inline, body_only = plan_comments(candidates, current, diffable)
    urls = submit_review(gh, repo, pr, head_sha, review_body, inline, bot_login)

Every function takes a `gh` callable `gh(*args, stdin=None) -> str` as its
first parameter — the only GitHub seam; tests inject a double that refuses
any call it does not model and records the calls it does. `stdin` carries a
write's JSON payload: a review body does not fit in an argument list.

A review or comment is the action's own only when its author is `bot_login`
— the login the run's token posts as — and the review's body carries
`MARKER` on a line of its own; marker text anywhere else never matches,
and any other login is never the action's. A comment is current when its
`commit_id` is the screened head and it replies to no other comment.

Each inline comment the module writes ends with a hidden machine-readable
field on a line of its own:

    <!-- pin-guard: quoted {"text": ...} -->

holding `{"text": <the exact line text the comment quotes>}`, with every
`&`, `<`, `>` and `-` escaped (`&amp;`, `&lt;`, `&gt;`, `&dash;`) so the
field can never close its own comment or leak markup; `current_bot_comments`
reads the field back into `quoted_text`, and a comment whose field does not
parse is skipped — it cannot be matched against candidates, so dedupe would
only guess at it.
"""
import json
import re
import subprocess

MARKER = '<!-- pin-guard: review -->'
_QUOTED_PREFIX = '<!-- pin-guard: quoted '
GUIDANCE = ('If this line pins a dependency, delete it, then resolve this thread. '
            'If it is a consistency check between files or steps, or a security invariant enumerated '
            'over every workflow, resolve the thread with a reply naming why.')

_ESCAPE = (('&', '&amp;'), ('<', '&lt;'), ('>', '&gt;'), ('-', '&dash;'))


def _escape(text):
    """Escape a hidden field's markup: `&` first, so the rest is reversible."""
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


def _own_author(node, bot_login):
    """The author is the login the run's token posts as, whatever `type` carries it."""
    return (node.get('user') or {}).get('login') == bot_login


def current_bot_comments(gh, repo, pr, head_sha, bot_login):
    """The action's own inline comments still current at `head_sha`.

    Reads the PR's inline comments and its reviews (both `--paginate
    --slurp`, which prints every page's array back-to-back as one array of
    pages — a listing read whole), flattens the pages, and keeps the
    comments whose author is `bot_login` AND whose parent review
    — matched by `pull_request_review_id` — is itself the action's own
    (`bot_login` author, `MARKER` on a line of its own), whose `commit_id`
    equals `head_sha` and whose `in_reply_to_id` is null. Each returned dict is
    the comment's API object plus `quoted_text`, the exact line text read
    from the hidden field `render_candidate` writes.

    shortcut: a review comment created at commit X cannot be outdated at X
    — outdatedness arises when the line changes relative to the comment's
    commit — so `commit_id == screened head ∧ in_reply_to_id is null`
    proves current at that head; observed on a real two-head pull request:
    commit_id moves to the new head.
    """
    comments = [item for page in json.loads(gh('api', f'repos/{repo}/pulls/{pr}/comments', '--paginate', '--slurp'))
                for item in page]
    reviews = {item['id']: item for page in json.loads(gh('api', f'repos/{repo}/pulls/{pr}/reviews', '--paginate', '--slurp'))
               for item in page}
    current = []
    for item in comments:
        source = reviews.get(item.get('pull_request_review_id'))
        if source is None or MARKER not in _own_lines(source.get('body')):
            continue
        if not _own_author(source, bot_login) or not _own_author(item, bot_login):
            continue
        if item.get('commit_id') != head_sha or item.get('in_reply_to_id') is not None:
            continue
        quoted = _read_quoted(item.get('body') or '')
        if quoted is None:
            continue
        current.append({**item, 'quoted_text': quoted})
    return current


def _read_quoted(body):
    """The quoted line text from the comment's hidden field, or None."""
    for line in _own_lines(body):
        if line.startswith(_QUOTED_PREFIX) and line.endswith(' -->'):
            try:
                data = json.loads(_unescape(line[len(_QUOTED_PREFIX):-len(' -->')]))
            except ValueError:
                continue
            if isinstance(data, dict) and isinstance(data.get('text'), str):
                return data['text']
    return None


def plan_comments(candidates, existing, diffable):
    """Route a screened head's candidates into new inline comments and body rows.

    `candidates` is the screened head's rows `[(path, line, text)]`;
    `existing` the current comments from `current_bot_comments`; `diffable`
    the set of `(path, line)` pairs anchorable on the PR diff. An existing
    comment with the same `path` AND the same exact quoted line text
    absorbs exactly one candidate occurrence — two identical candidates
    plus one existing comment plan one new comment — so an absorbed
    candidate produces nothing. A candidate whose only same-shaped earlier
    comment is outdated (a different `commit_id`, or a reply) is not in
    `existing` and is planned again. A candidate with a diffable anchor
    goes to `inline`, the rest to `body_only`; both are raw
    `(path, line, text)` rows.
    """
    inline, body_only = [], []
    spare = list(existing)
    for path, line, text in candidates:
        match = next((item for item in spare
                      if item.get('path') == path and item.get('quoted_text') == text), None)
        if match is not None:
            spare.remove(match)
            continue
        (inline if (path, line) in diffable else body_only).append((path, line, text))
    return inline, body_only


def render_candidate(text):
    """A candidate line rendered for a comment body: the text inert in a code fence, then the guidance.

    The fence's delimiter is a run of backticks longer than any run inside
    the text (longest run + 1, minimum 3), so no line of the text can close
    it and `@mentions`, `#N` references, `-->` and raw HTML stay literal.
    """
    longest = max((len(run) for run in re.findall(r'`+', text)), default=0)
    fence = '`' * max(longest + 1, 3)
    quoted = _escape(json.dumps({'text': text}, ensure_ascii=True))
    return f'{fence}\n{text}\n{fence}\n\n{GUIDANCE}\n\n{_QUOTED_PREFIX}{quoted} -->'


def submit_review(gh, repo, pr, head_sha, review_body, inline, bot_login):
    """Post the screened head's review(s) and return their URLs.

    POSTs `repos/{repo}/pulls/{pr}/reviews` with `{event: 'COMMENT',
    commit_id: head_sha, body: review_body, comments: [{path, line,
    side: 'RIGHT', body}, ...]}`, the payload on stdin. When GitHub refuses
    an oversized review with a 422, the comment set is split in half and
    each half retried against a fresh review, recursively — every candidate
    lands in some review, none silently dropped; a 422 on a single comment
    cannot split and is raised. Each accepted review's `html_url` is
    returned. `bot_login` names the identity the review posts as; owning
    the posted review later is `current_bot_comments`' reading, which
    matches the same login.
    """
    return _post_reviews(gh, repo, pr, head_sha, review_body, list(inline))


def _post_reviews(gh, repo, pr, head_sha, review_body, comments):
    endpoint = f'repos/{repo}/pulls/{pr}/reviews'
    payload = json.dumps({'event': 'COMMENT', 'commit_id': head_sha, 'body': review_body,
                          'comments': [{'path': path, 'line': line, 'side': 'RIGHT',
                                        'body': render_candidate(text)}
                                       for path, line, text in comments]}, ensure_ascii=True)
    try:
        out = gh('api', endpoint, '--method', 'POST', '--input', '-', stdin=payload)
    except subprocess.CalledProcessError as error:
        if '422' not in (error.stderr or '') or len(comments) <= 1:
            raise
        mid = len(comments) // 2
        return (_post_reviews(gh, repo, pr, head_sha, review_body, comments[:mid])
                + _post_reviews(gh, repo, pr, head_sha, review_body, comments[mid:]))
    return [json.loads(out)['html_url']]
