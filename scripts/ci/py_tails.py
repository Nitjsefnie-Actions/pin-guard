"""first_tree: the first start of a hunk whose tail parses."""
import ast
import codeop
import re
import textwrap
import warnings

_LEXEME = re.compile(r'\\.|"""|' "'''" r'''|["'#()\[\]{}]''')
_DEFINES = re.compile(r'@|(?:async\s+)?def\b|class\b')
_CLAUSE = re.compile(r'(?:else|elif|except|finally)\b')


def first_tree(chunk, starts, parse):
    code = [len(chunk)] * (len(chunk) + 1)  # the first row at or after each with a token
    for row in range(len(chunk) - 1, -1, -1):
        text = chunk[row][2]  # an indented \\ indents its joined line
        code[row] = code[row + 1] if text.isspace() or text.lstrip(' \t\x0c')[:1] in ('', '#') or text == '\\' else row
    views, made, pending, opening, known = [], False, [], set(starts), set()
    for offset in starts:  # view: scan from a failing start, next row, last start, statement start
        for view in views:
            while view[1] < offset:
                view[1] = next(view[0], len(chunk))
        views, at = [view for view in views if view[1] < len(chunk)], [view for view in views if view[1] == offset]
        deferred = code[offset] != offset and (code[offset] in opening or code[offset] == len(chunk))
        if offset in known or deferred and code[offset] in known or any(_chains(chunk, code, view, offset) for view in at):
            known.add(code[offset])
            for view in at:
                view[2] = offset
            pending = []
            continue
        if deferred and code[offset] < len(chunk):
            pending.append(offset)
            continue
        whole = False
        if not _fails_early(chunk, offset):
            try:
                tree = parse(textwrap.dedent('\n'.join(text for _, _, text in chunk[offset:])))
            except SyntaxError:
                whole = True
            else:
                for first in pending:
                    try:
                        return first, parse(textwrap.dedent('\n'.join(text for _, _, text in chunk[first:])))
                    except SyntaxError:
                        pass
                return offset, tree
        pending = []
        if deferred:
            known.add(code[offset])
        for view in at:
            view[2] = offset
        if whole or not made:
            views, made = (views + [[_boundaries(chunk, offset), offset, offset, offset]])[-8:], True
    return None


def _chains(chunk, code, view, offset):
    _, _, last, begin = view
    lead = chunk[code[offset]][2].lstrip() if code[offset] < len(chunk) else ''
    joined = _joins(chunk[last:offset]) or bool(_DEFINES.match(lead)) and _decorates(chunk[last:offset])
    if not joined and begin is not None and begin != last:
        joined = _joins(chunk[begin:offset])
        begin = view[3] = None if joined is None else begin
    if joined:
        view[2:] = [offset, begin if _CLAUSE.match(lead) else offset]
    return bool(joined)


def _boundaries(chunk, origin):
    """Rows past origin a rough scan puts between lines; speed only (3.15.1 tokenize reads out of bounds)."""
    depth, quote, joined = 0, '', False
    for row in range(origin, len(chunk)):
        if row > origin and not (depth or quote or joined):
            yield row
        text = chunk[row][2]
        for match in _LEXEME.finditer(text):
            lexeme = match[0]
            if quote:
                quote = '' if lexeme == quote else quote
            elif lexeme == '#':
                break
            elif lexeme[0] in '\'"':
                quote = lexeme
            elif lexeme in '([{':
                depth += 1
            elif lexeme in ')]}':
                depth = max(depth - 1, 0)
        joined = text.endswith('\\') and not text.lstrip().startswith('#')
        quote = quote if len(quote) == 3 or joined else ''


def _compiles(lines, suffix=''):
    """The module, 0 if incomplete, None if failing before the end."""
    try:
        with warnings.catch_warnings(action='ignore'):
            return compile('\n'.join('' if text.isspace() else text for _, _, text in lines) + suffix, '<unknown>', 'exec',
                           ast.PyCF_ONLY_AST | vars(codeop)['PyCF_ALLOW_INCOMPLETE_INPUT'])
    except SyntaxError as exc:
        return 0 if type(exc).__name__ == '_IncompleteInputError' else None


def _fails_early(chunk, offset):
    """A prefix failing short of its end fails it all (Parser/pegen.c)."""
    size = 1
    while chunk[offset][2][:1].strip() and 4 * size < len(chunk) - offset:
        if _compiles(chunk[offset:offset + size]) is None:
            return True
        size *= 4
    return False


def _joins(lines):
    """True if they parse alone (barring barry_as_FLUFL, Parser/action_helpers.c), None if failing early."""
    tree = _compiles(lines)
    return tree and not any(isinstance(node, ast.ImportFrom) and node.module == '__future__'
                            and any(alias.name == 'barry_as_FLUFL' for alias in node.names) for node in ast.walk(tree))


def _decorates(lines):
    """Decorator rows share one def or class verdict (Grammar/python.gram)."""
    tree = _compiles(lines, '\nclass _: pass')
    return bool(tree) and len(tree.body) == 1 and isinstance(tree.body[0], ast.ClassDef) and bool(tree.body[0].decorator_list)
