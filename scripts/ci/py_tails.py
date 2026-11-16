"""first_tree: the first start of a hunk whose tail parses; the rest are settled exactly, often unparsed."""
import ast
import codeop
import textwrap
import warnings


def first_tree(chunk, starts, parse):
    known, anchor, gap = set(), None, 1
    for i, (offset, end) in enumerate(zip(starts, starts[1:] + [len(chunk)])):
        before = starts[i - 1] if i else None
        if before in known and (_alone(chunk[before:offset], parse) or _alone(chunk[before:offset], parse, True)
                                and _alone(chunk[offset:end], parse, True)):
            known.add(offset)
        elif anchor is not None and offset - anchor >= gap:
            if _alone(chunk[anchor:offset], parse):
                known.add(offset)
            gap *= 2
        if offset in known:
            anchor, gap = offset, 1
            continue
        if not _fails_early(chunk, offset):
            try:
                return offset, parse(textwrap.dedent('\n'.join(text for _, _, text in chunk[offset:])))
            except SyntaxError:
                pass
        known.add(offset)
        anchor = offset if anchor is None else anchor
    return None


def _fails_early(chunk, offset):
    """A prefix failing short of its end fails it all (Parser/pegen.c)."""
    size = 1
    while chunk[offset][2][:1].strip() and 4 * size < len(chunk) - offset:
        try:
            with warnings.catch_warnings(action='ignore'):
                compile('\n'.join('' if text.isspace() else text for _, _, text in chunk[offset:offset + size]),
                        '<unknown>', 'exec', ast.PyCF_ONLY_AST | vars(codeop)['PyCF_ALLOW_INCOMPLETE_INPUT'])
        except SyntaxError as exc:
            if type(exc).__name__ != '_IncompleteInputError':
                return True
        size *= 4
    return False


def _alone(lines, parse, decorators=False):
    """Rows parsing alone hand a tail's failure on; decorator rows share one def or class verdict (Grammar/python.gram)."""
    try:
        tree = parse('\n'.join('' if text.isspace() else text for _, _, text in lines) + '\nclass _: pass' * decorators)
    except SyntaxError:
        return False
    if decorators:
        return len(tree.body) == 1 and isinstance(tree.body[0], ast.ClassDef) and bool(tree.body[0].decorator_list)
    return not any(isinstance(node, ast.ImportFrom) and node.module == '__future__' for node in ast.walk(tree))
