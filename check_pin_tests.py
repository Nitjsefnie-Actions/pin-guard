#!/usr/bin/env python3
"""List candidate test lines a pull request adds that pin a dependency: a workflow step, a
Dockerfile base image or a tool version.

    check_pin_tests.py --repo OWNER/REPO --pr N

The screen implements the repository's own rule — no test asserts on a pinned version,
tag, SHA or digest of any dependency: a workflow `uses:` value (the action's SHA,
release, version comment or pin shape) or its `with:` inputs, a Dockerfile `FROM`
base image, a tool version or a package version — and a legitimate bump must never
fail a test; the screen lists candidates for deletion or rewriting. Consistency
checks derived from the pinned files and security invariants enumerated over
them stay. This is a screen, not a verdict: it lists candidates; a reader
judges each one against the rule.

A test file is anything under a `tests`/`test`/`__tests__` directory or named
`test_*`, `*_test.*`, `*.test.*` or `*.spec.*`. Its added lines are grouped into
statements (an assertion or assignment's open brackets outside strings join
the lines that follow, up to ten lines, extended through standalone string
literals). A test file is screened against a pin
source only when it reads that source: its text
calls a reader (`open(`, `read_text(`, `readFileSync(`, `glob(` ...) and names
the source. That one predicate serves every source. A test file whose code
loads this screen itself (`import check_pin_tests`, `from check_pin_tests
import ...`, or `spec_from_file_location('check_pin_tests', <a path whose last
component is check_pin_tests.py>)`; a comment, a string or a similarly named
module loads nothing) is the screen's own suite: its fixture text is input to
the screen, so it reads no source, unless its code outside string literals
names that source in a `/` join or a path or reader call (`open`, `Path`,
`os.path.join`, `glob` ...). A statement is listed when:
  - its file reads the workflows and it asserts while reading a step's `uses` or
    `with` (`["uses"]`, `.uses`, `.get("with")`, `toHaveProperty("uses")`)
    against a literal value. Python comparisons and unittest calls are read
    with ast; reads of files an executed step wrote under a `/`-joined path built
    from an input, and a string composed around an input compared with a value
    read from another step (`_step(...)["run"]`), a value derived by a call handed
    a workflow, action or Dockerfile path compared with an input, an input compared
    with a value a call derives from a field of a different step
    (`re.findall(..., stage["run"])[0]`, inline or through a bound name) whose step
    is read from the workflows (a `["steps"]` read off a call handed no literal,
    an index or `next(...)` selection from one, or a name already read from the
    workflows), not from literal fixture text and not the input's own step;
    absence checks, action-identity checks with the revision removed,
    step-order checks selected by action identity, name or input presence,
    and consistency checks against computed values stay. Literal `${{ }}`
    comparisons, including whitespace-stripped ones and `secrets.*`, are listed.
    Single-file credential/history checks, including Python substring checks
    for `persist-credentials:`, `fetch-depth:` and `run-id:`, are listed.
    Visible Python enumeration sources are glob/rglob of `.github/workflows`
    with `*.yml`, `*.yaml`, `*.y*ml` or `*` (also as a root-relative glob argument),
    rglob('action.yml') on the root or `.github/actions`, and subprocess
    run/check_output of `git ls-files` with the `action.yml` pathspec (optionally
    also `*/action.yml`). Module and local Name bindings resolve to a fixpoint;
    sorted/list/tuple wrappers, append/extend/+= accumulation, comprehensions
    and tuple unpacking preserve the provenance of each element separately.
    Reads through read_text/read_bytes, _workflow, load/safe_load and
    workflow_steps/workflow_jobs carry a member into its jobs and steps.
    Credential-false and event run-id checks on those members stay, including
    whitespace-normalised event expressions, in assert or
    assertIs/assertEqual/assertTrue/assertIn unittest forms; assertFalse and
    assertNotIn already stay as absence checks. Path dependencies follow plain,
    augmented, annotated, walrus, tuple and comprehension bindings to a fixpoint,
    separately from resolved values; parsed content does not acquire path taint.
    Path-dependent if/while tests and match subjects/guards, try bodies mentioning
    a path whose handlers continue/break, and unguarded breaks keep checks listed.
    Continue/break under a path-independent content condition stays eligible;
    raise, return and yield/yield-from in the loop keep checks listed, while nested
    function bodies do not narrow their enclosing loop. Class bodies execute and
    are inspected with their own path aliases. File lists, narrower
    patterns and recognised literal fixture directories confer no exemption.
    An unresolved root Name is assumed to be the repository root; this is not
    proof that it is outside a fixture directory. Other unresolved values confer
    no exemption. The --pr command supplies its fetched head test
    files as unchanged context; only added statements are listed. Other hunks
    are dedented or parsed from a complete top-level row; unseen loops confer
    no exemption. This recognition
    does not validate the invariant's coverage or nonzero count. Visible local
    assignments track input aliases, computed pins, and steps parsed from
    literal fixture text by workflow_steps/workflow_jobs. Other languages use
    bounded text matching; expect messages are excluded, as are dynamic template
    values, no-checkout predicates and the SHA-stripping identity shape; or
  - its file reads workflows, actions, Dockerfiles or compose files and a Python
    regex assertion or expect(...).toMatch(/.../) tests a 40-hex SHA, a
    sha256:64-hex digest or an action ref containing a 40-hex SHA. Python
    match/search/fullmatch, assertRegex and visible compiled patterns count;
    a computed digest equality does not; or
  - its file reads the workflows and it holds a SHA a workflow pins or asserts
    on a version reference (`@v1`, `# v1.0.0`); or
  - it holds, inside a quoted string, a literal pinned in a source its file
    reads (a comment line or bare prose holds none):
      .github/workflows/*.yml|yaml: the version of an inline `pip install x==y`
        or `npm i|install|add x@y` on a run line;
      Dockerfile, *.Dockerfile, Containerfile: each FROM image's `name:tag` (a
        tag carrying a digit), the tag alone when it carries a dotted version,
        and its sha256 digest; FROM flags are skipped, ARG defaults (`${X}`,
        `$X`, `${X:-d}`) are substituted, and stage aliases and scratch pin
        nothing;
      requirements*.txt: the version of each `==` or `===` pin;
      package.json: the exact versions in dependencies, devDependencies,
        peerDependencies, optionalDependencies and engines, and packageManager's;
      .tool-versions, .python-version, .nvmrc: each version they name.
    A literal is matched whole (1.2.3 is not held by 11.2.3, 1.2.34 or 1.2.3.4;
    a `v` before it is allowed). Pins are read from those files at the PR head
    and from the diff itself (a package.json hunk only when it parses whole).

It misses every pin it cannot read as a literal from a source it reads: a value
built at runtime or split across statements beyond its local assignment tracking
(a binding outside the diff, a tag's version held without its suffix), a literal
wrapped in a call (`== str("x")`), an input read through an alias before the comparison, a literal
outside a quoted string (a bare shell word, a number), a version with no dot, a FROM whose ARG has no
default, and every source it does not read (other Dockerfile names, lockfiles,
pyproject.toml, setup.cfg, other installers on run lines), and a read in the
screen's own suite whose path is only a name bound elsewhere (`WF = '...'`,
then `open(WF)`). It also lists a
quoted version that merely equals a pinned one, for the reader to judge. So an
empty list is not a proof. A pin source it cannot fetch or parse, or a test file
it cannot fetch, is named on stderr as `could not screen <path>: <why>` and is
never counted as screened. Test fragments that ast cannot parse use the text
matcher.

Exit 0: no candidates. Exit 1: candidates, one `path:line: text` each. Exit 2:
the PR could not be read, the screen crashed (`could not screen <repo> PR <n>: <error>`), or no candidates were found and some file could not
be screened. On exit 0 and 1, stderr ends with a summary that says what the
screen cannot see.
"""
import argparse
import ast
import base64
import json
import re
import subprocess
import sys
import textwrap
import warnings

def _parse(text):
    """ast.parse of a scanned repo's code, without its SyntaxWarnings ("\\$" in a
    non-raw string) burying the verdict on stderr."""
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', SyntaxWarning)
        return ast.parse(text)


_HUNK = re.compile(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@')
_ACTION_PIN = re.compile(r'@([0-9a-f]{40})\b')
_SHA = re.compile(r'\b([0-9a-f]{40})\b')
_WITH_KEY = re.compile(r"""\[\s*["']with["']\s*\]|\.with\b|\.get\(\s*["']with["']""")
_KEY = re.compile(r"""\[\s*["'](?:uses|with)["']\s*\]|\.uses\b|\.with\b|\.get\(\s*["'](?:uses|with)["']"""
                  r"""|toHaveProperty\(\s*["'](?:uses|with)["']""")
_VERSION_REF = re.compile(r'@v\d|#\s*v\d+(?:\.\d+)*\b|@[0-9a-f]{40}\b')
_ASSERTION = re.compile(r'\bassert|\bexpect\s*\(|\.to(?:Be|Equal|Match|Contain|HaveProperty)\b|\bshould\b'
                        r'|\bgrep\s+-[a-zA-Z]*q')
_ABSENCE = re.compile(r'\.not\.|\bnot in\b|assertNotIn|\bis None\b|assertIsNone|assertFalse|\bnot any\(|'
                      r'\ball\(\s*not\b|toHaveLength\(0\)|==\s*\[\]|==\s*\{\}')
_SUBSCRIPT = re.compile(r'''\[\s*["'][^"'\]]*["']\s*\]|\.get\(\s*["'][^"']*["']|\$\{[^}]*\}''')
_CALL = re.compile(r'(?<![\w.])(?![\w.]*(?:assert|expect)\w*\()(?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*\([^()]*\)')
_LITERAL = re.compile(r'''["'`][^"'`]+["'`]|\b(?:True|False|true|false|None|null)\b|(?<![\w.])\d+(?![\w.])''')
_OTHER_STEP = re.compile(r"""\.env\b|\[\s*["']env["']\s*\]|\bdeclared\b|\binputs\b|issubset|<=""")
_SPAN_MAX = 10
_READS = re.compile(r'\b(?:readFileSync|readFile|read_text|read_bytes|open|glob|readdirSync|readdir|listdir|iterdir|'
                    r'safe_load|load_workflow\w*)\s*\(')
_SOURCES = {  # what a test file names when it reads each pin source
    'workflow': re.compile(r'\.github/workflows|workflows/'),
    'dockerfile': re.compile(r'Dockerfile|Containerfile'),
    'pin-shape': re.compile(r'\.github/(?:workflows|actions)|workflows/|action\.ya?ml|Dockerfile|Containerfile|(?:docker-)?compose[\w.-]*\.ya?ml'),
    'requirements': re.compile(r'requirements[\w.-]*\.txt'),
    'package.json': re.compile(r'package\.json'),
    '.tool-versions': re.compile(r'\.tool-versions'),
    '.python-version': re.compile(r'\.python-version'),
    '.nvmrc': re.compile(r'\.nvmrc'),
}
_DOTTED = re.compile(r'\d+\.\d+')
_STRING = re.compile(r'''"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`''')
_COMMENT = re.compile(r'\s*(?:#|//|/\*|\*)')
_ARG_REF = re.compile(r'\$\{(\w+)(?::-([^}]*))?\}|\$(\w+)')
_DEP_KEYS = ('dependencies', 'devDependencies', 'peerDependencies', 'optionalDependencies', 'engines')
_EXACT = re.compile(r'[v=]?(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?)')
_PIP_PIN = re.compile(r'''(?<![\w.-])[A-Za-z0-9][\w.-]*(?:\[[^\]]*\])?===?([^\s'"`;,)]+)''')
_NPM_PIN = re.compile(r'(?<![\w@/.-])@?[\w.-]+(?:/[\w.-]+)?@(\d+\.\d+[\w.+-]*)')
_TEST_DIR = re.compile(r'(^|/)(tests?|__tests__)/')
_TEST_NAME = re.compile(r'(^|/)(test_[^/]*|[^/]*_test\.[^/]*|[^/]*\.(test|spec)\.[^/]*)$')


def is_test_file(path):
    return bool(_TEST_DIR.search(path) or _TEST_NAME.search(path))


def _lines(diff_text):
    """Yield hunk rows as (path, line, kind, text); '@' marks a hunk boundary."""
    path, line = None, 0
    for raw in diff_text.splitlines():
        if raw.startswith('+++ '):
            target = raw[4:]
            path = target[2:] if target.startswith('b/') else None
            continue
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


def _reads(node):
    """ast.walk minus the operands of a `/` join: a path built from an input names a
    location, and what a test reads back from it is the output of an executed step."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return
    yield node
    for child in ast.iter_child_nodes(node):
        yield from _reads(child)


def _field(node, bindings):
    """The workflow field an expression reads, including a locally assigned input alias."""
    for part in _reads(node):
        if isinstance(part, ast.Name) and bindings.get(part.id) == 'with':
            return 'with'
        key = None
        if isinstance(part, ast.Attribute):
            key = part.attr
        if isinstance(part, ast.Subscript) and isinstance(part.slice, ast.Constant):
            key = part.slice.value
        if isinstance(part, ast.Call) and isinstance(part.func, ast.Attribute) and part.func.attr == 'get' and part.args:
            key = part.args[0].value if isinstance(part.args[0], ast.Constant) else None
        if key in ('uses', 'with'):
            return key
    return None


def _reads_source(call):
    """Whether a call is handed the path of a workflow, action or Dockerfile: it reads the pin source."""
    return any(isinstance(p, ast.Constant) and isinstance(p.value, str) and _SOURCES['pin-shape'].search(p.value)
               for arg in call.args for p in ast.walk(arg))


def _derived(node, bindings):
    return any(isinstance(p, ast.Call) and isinstance(p.func, ast.Name)
               and p.func.id in ('_shipped_action_pin', 'shipped_pin')
               or isinstance(p, ast.Call) and _reads_source(p)
               or _field(p, bindings)
               or isinstance(p, ast.Call) and isinstance(p.func, ast.Attribute)
               and p.func.attr in ('read_text', 'read_bytes')
               or isinstance(p, ast.Name) and bindings.get(p.id) == 'derived'
               for p in ast.walk(node))


def _root(node):
    """The name a chain of subscripts, attributes and `.get` calls reads from, or None."""
    while True:
        if isinstance(node, (ast.Subscript, ast.Attribute)):
            node = node.value
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'get':
            node = node.func.value
        else:
            return node.id if isinstance(node, ast.Name) else None


def _step_value(node, bindings):
    """Whether an expression is a workflow step, or a steps list, read from the parsed workflows: a
    `["steps"]` read off a call handed no literal, or an index or `next(...)` selection from one."""
    if isinstance(node, ast.Name):
        return bindings.get(node.id) == 'step'
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'next'
            and node.args and isinstance(node.args[0], ast.GeneratorExp)):
        return _step_value(node.args[0].generators[0].iter, bindings)
    if not isinstance(node, ast.Subscript):
        return False
    if not (isinstance(node.slice, ast.Constant) and node.slice.value == 'steps'):
        return not (isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str)) \
            and _step_value(node.value, bindings)
    root = node.value
    while isinstance(root, ast.Subscript):
        root = root.value
    return isinstance(root, ast.Call) and not any(
        _literal(arg, bindings) or isinstance(arg, ast.Name) and bindings.get(arg.id) in ('literal', 'fixture')
        for arg in root.args)


def _step_fields(node, bindings):
    """The steps (names bound to a step or a value read from the workflows) whose field a call in the
    expression is handed, directly or through a name bound to such a call's result."""
    names = set()
    for p in ast.walk(node):
        if isinstance(p, ast.Name):
            names |= bindings.get(('step-fields', p.id), set())
        if isinstance(p, ast.Call):
            names.update(q.value.id for arg in p.args for q in ast.walk(arg)
                         if isinstance(q, ast.Subscript) and isinstance(q.slice, ast.Constant)
                         and isinstance(q.slice.value, str) and isinstance(q.value, ast.Name)
                         and bindings.get(q.value.id) in ('step', 'derived'))
    return names


def _literal(node, bindings):
    """Whether a comparison operand supplies a held value rather than a computed one."""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.Dict, ast.List, ast.Set, ast.Tuple)):
        return all(_literal(child, bindings) for child in ast.iter_child_nodes(node)
                   if not isinstance(child, ast.expr_context))
    if isinstance(node, ast.JoinedStr):
        return not any(_derived(value.value, bindings)
                       for value in node.values if isinstance(value, ast.FormattedValue))
    if isinstance(node, ast.BinOp):
        return _literal(node.left, bindings) and _literal(node.right, bindings)
    return False


def _identity(node):
    """Recognize a stripped action identity, including the cited list projection and Path.name."""
    if isinstance(node, ast.ListComp):
        return _identity(node.elt) and not any(_field(test, {}) for gen in node.generators for test in gen.ifs)
    if (isinstance(node, ast.Attribute) and node.attr == 'name' and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name) and node.value.func.id == 'Path'
            and len(node.value.args) == 1):
        return _identity(node.value.args[0])
    return (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and node.slice.value == 0
            and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr in ('split', 'partition') and node.value.args
            and isinstance(node.value.args[0], ast.Constant) and node.value.args[0].value == '@'
            and _field(node.value.func.value, {}) == 'uses')


def _fixture_operand(node, bindings):
    while isinstance(node, (ast.Subscript, ast.Attribute)):
        node = node.value
    return isinstance(node, ast.Name) and bindings.get(node.id) == 'fixture'


def _pin_pattern(node, bindings):
    if isinstance(node, ast.Name):
        return bindings.get(node.id) == 'pin-pattern'
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'compile' and node.args:
        return _pin_pattern(node.args[0], bindings)
    return (isinstance(node, ast.Constant) and isinstance(node.value, str)
            and bool(re.search(r'\[(?:0-9a-f|a-f0-9)\]\{40\}|sha256:\[(?:0-9a-f|a-f0-9)\]\{64\}', node.value)))


def _pin_shape(text, bindings):
    try:
        statement = _parse(text).body[0]
    except (SyntaxError, IndexError):
        # `.not.toMatch(...)` asserts the shape is absent, which pins nothing.
        return bool(re.match(r'expect\s*\(', text) and re.search(r'(?<!\.not)\.toMatch\(\s*/(?:\\.|[^/\\\n])*?(?:\[(?:0-9a-f|a-f0-9)\]\{40\}'
                              r'|sha256:\[(?:0-9a-f|a-f0-9)\]\{64\})', text))
    if isinstance(statement, ast.Assert):
        node = statement.test
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return False  # `assert not re.search(...)` asserts absence, not a pin
    elif (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)
          and isinstance(statement.value.func, ast.Attribute) and statement.value.func.attr == 'assertRegex'):
        return len(statement.value.args) >= 2 and _pin_pattern(statement.value.args[1], bindings)
    else:
        return False
    return any(isinstance(p, ast.Call) and isinstance(p.func, ast.Attribute)
               and p.func.attr in ('match', 'fullmatch', 'search')
               and (_pin_pattern(p.func.value, bindings) or p.args and _pin_pattern(p.args[0], bindings))
               for p in ast.walk(node))


def _step_order(test, bindings):
    """Only index ordering with identity/presence selectors, never input-value selectors."""
    order = test
    if (isinstance(test, ast.Call) and isinstance(test.func, ast.Name) and test.func.id == 'all'
            and len(test.args) == 1 and isinstance(test.args[0], ast.GeneratorExp)):
        order = test.args[0].elt
    if not (isinstance(order, ast.Compare) and all(isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE))
                                                 for op in order.ops)):
        return False
    if _field(order.left, bindings) or any(_field(p, bindings) for p in order.comparators if not isinstance(p, ast.Call)):
        return False
    for node in ast.walk(test):
        if isinstance(node, ast.Compare) and node is not order and _field(node, bindings):
            if not (len(node.ops) == 1 and isinstance(node.ops[0], ast.In)
                    and isinstance(node.left, ast.Constant) and isinstance(node.left.value, str)
                    and isinstance(node.comparators[0], ast.Call)
                    and isinstance(node.comparators[0].func, ast.Attribute)
                    and node.comparators[0].func.attr == 'get'
                    and node.comparators[0].args and isinstance(node.comparators[0].args[0], ast.Constant)
                    and node.comparators[0].args[0].value == 'with'):
                return False
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and _field(node.func.value, bindings):
            if not (node.func.attr == 'startswith' and _field(node.func.value, bindings) == 'uses'
                    and len(node.args) == 1 and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str) and node.args[0].value.endswith('@')):
                return False
    return True


def _invariant_test(statement):
    if isinstance(statement, ast.Assert):
        return statement.test
    if not (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)
            and isinstance(statement.value.func, ast.Attribute)):
        return None
    call = statement.value
    operators = {'assertIs': ast.Is, 'assertEqual': ast.Eq, 'assertIn': ast.In}
    if call.func.attr in operators and len(call.args) >= 2:
        return ast.Compare(left=call.args[0], ops=[operators[call.func.attr]()], comparators=[call.args[1]])
    if call.func.attr == 'assertTrue' and call.args:
        return call.args[0]
    return None


class _Enumeration:
    """Resolve visible enumeration data without executing the test.

    Values distinguish path fragments, members, collection elements and tuple
    slots; member key sets carry '@path' until read. None means unresolved,
    and 'empty' is an empty collection.
    """

    @staticmethod
    def element(value):
        if isinstance(value, frozenset):
            return value
        if isinstance(value, tuple) and value[0] == 'collection':
            return value[1]
        if isinstance(value, tuple) and value[0] == 'tuple':
            result = 'empty'
            for part in value[1]:
                result = _Enumeration.merge(result, part)
            return result
        return None

    @staticmethod
    def collection(value):
        return value if value == 'empty' else ('collection', _Enumeration.element(value))

    @staticmethod
    def merge(left, right):
        if left == right:
            return left
        if isinstance(left, frozenset) and isinstance(right, frozenset):
            if ('@path' in left) != ('@path' in right):
                return None
            return left & right
        if left == 'empty':
            return right
        if right == 'empty':
            return left
        if isinstance(left, tuple) and isinstance(right, tuple) and left[0] == right[0] == 'collection':
            return ('collection', _Enumeration.merge(left[1], right[1]))
        if (isinstance(left, tuple) and isinstance(right, tuple) and left[0] == right[0] == 'tuple'
                and len(left[1]) == len(right[1])):
            return ('tuple', tuple(_Enumeration.merge(a, b) for a, b in zip(left[1], right[1])))
        return None

    @staticmethod
    def bind(target, value, env):
        if isinstance(target, ast.Name):
            env[target.id] = value
        elif isinstance(target, (ast.Tuple, ast.List)):
            slots = value[1] if isinstance(value, tuple) and value[0] == 'tuple' else ()
            for i, child in enumerate(target.elts):
                _Enumeration.bind(child, slots[i] if len(slots) == len(target.elts) else None, env)
        elif isinstance(target, (ast.Subscript, ast.Attribute)):
            _Enumeration.bind(target.value, None, env)

    def value(self, node, env):
        if isinstance(node, ast.Name):
            return env.get(node.id)
        if isinstance(node, ast.Tuple):
            return ('tuple', tuple(self.value(p, env) for p in node.elts))
        if isinstance(node, ast.List):
            result = 'empty'
            for part in node.elts:
                result = self.merge(result, ('collection', self.value(part, env)))
            return result
        if isinstance(node, (ast.ListComp, ast.GeneratorExp)):
            local = env.copy()
            for gen in node.generators:
                self.bind(gen.target, self.element(self.value(gen.iter, local)), local)
                if gen.is_async or self.filtered(gen.ifs, local):
                    return None
            return ('collection', self.value(node.elt, local))
        if isinstance(node, ast.Attribute):
            base = self.value(node.value, env)
            return base if isinstance(base, frozenset) or node.attr == 'stdout' else None
        if isinstance(node, ast.Subscript):
            base = self.value(node.value, env)
            key = node.slice.value if isinstance(node.slice, ast.Constant) else None
            return base | {key} if isinstance(base, frozenset) and isinstance(key, str) else None
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            value = self.value(node.right, env)
            if isinstance(value, frozenset):
                return value
            if isinstance(self.value(node.left, env), frozenset):
                return None
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in ('sorted', 'list', 'tuple') and len(node.args) == 1:
                return self.value(node.args[0], env)
            if isinstance(func, ast.Attribute) and func.attr in ('glob', 'rglob') and len(node.args) == 1:
                base = self.value(func.value, env)
                if isinstance(base, tuple) and base[0] == 'path':
                    parts = list(base[1])
                elif base is None and isinstance(func.value, ast.Name):
                    parts = []
                else:
                    return None
                if parts and parts[0].startswith('/'):
                    parts = parts[1:]
                directory = '/'.join(parts)
                pattern_value = self.value(node.args[0], env)
                pattern = pattern_value[1][0] if (isinstance(pattern_value, tuple)
                                                and pattern_value[0] == 'path' and len(pattern_value[1]) == 1) else None
                if (directory == '.github/workflows' and pattern in ('*.yml', '*.yaml', '*.y*ml', '*')
                        or not directory and pattern in ('.github/workflows/*.yml', '.github/workflows/*.yaml',
                                                        '.github/workflows/*.y*ml', '.github/workflows/*')
                        or directory in ('', '.github/actions') and func.attr == 'rglob' and pattern == 'action.yml'):
                    return ('collection', frozenset({'@path'}))
                return None
            if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                    and func.value.id == 'subprocess' and func.attr in ('run', 'check_output') and node.args
                    and isinstance(node.args[0], (ast.List, ast.Tuple))):
                argv = [p.value if isinstance(p, ast.Constant) else None for p in node.args[0].elts]
                specs = argv[2:]
                if specs[:1] == ['--']:
                    specs = specs[1:]
                if argv[:2] == ['git', 'ls-files'] and 'action.yml' in specs and all(
                        p in ('action.yml', '*/action.yml') for p in specs):
                    return ('collection', frozenset({'@path'}))
                return None
            if isinstance(func, ast.Attribute):
                base = self.value(func.value, env)
                if isinstance(base, frozenset) and func.attr == 'get' and node.args:
                    key = node.args[0].value if isinstance(node.args[0], ast.Constant) else None
                    return base | {key} if isinstance(key, str) else None
                if isinstance(base, frozenset) and func.attr in ('values', 'read_text', 'read_bytes', 'split', 'replace'):
                    return base - {'@path'} if func.attr in ('read_text', 'read_bytes') else base
                if isinstance(base, frozenset) and func.attr == 'items':
                    return ('collection', ('tuple', (base, base)))
                if func.attr == 'splitlines' and isinstance(base, tuple) and base[0] == 'collection':
                    return base
                if (func.attr == 'join' and node.args and isinstance(func.value, ast.Constant)
                        and isinstance(self.value(node.args[0], env), frozenset)):
                    return self.value(node.args[0], env)
            parser = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            if (parser in ('_workflow', 'load', 'safe_load', 'workflow_steps', 'workflow_jobs')
                    and node.args and isinstance(self.value(node.args[0], env), frozenset)):
                return self.value(node.args[0], env) - {'@path'}
        paths = {name: list(value[1]) for name, value in env.items()
                 if isinstance(value, tuple) and value[0] == 'path'}
        parts = _path_parts(node, paths)
        return ('path', tuple(parts)) if parts else None

    def path_condition(self, node, env):
        if isinstance(node, ast.NamedExpr):
            dependent = self.path_condition(node.value, env)
            if dependent:
                self.bind(node.target, frozenset({'@path'}), env)
            return dependent
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            local = env.copy()
            dependent = False
            for gen in node.generators:
                member = self.element(self.value(gen.iter, local))
                if member is None and self.path_condition(gen.iter, local):
                    member = frozenset({'@path'})
                self.bind(gen.target, member, local)
                for condition in gen.ifs:
                    dependent |= self.path_condition(condition, local)
            values = (node.key, node.value) if isinstance(node, ast.DictComp) else (node.elt,)
            for value in values:
                dependent |= self.path_condition(value, local)
            for part in self.scope_nodes([node]):
                if isinstance(part, ast.NamedExpr) and self.path_condition(part.target, local):
                    self.bind(part.target, frozenset({'@path'}), env)
            return dependent
        value = self.value(node, env)
        if isinstance(value, frozenset):
            return '@path' in value
        return any(self.path_condition(child, env) for child in ast.iter_child_nodes(node)
                   if isinstance(child, ast.expr))

    def filtered(self, conditions, env):
        return any(self.path_condition(node, env) for node in conditions)

    def path_aliases(self, body, env):
        local = env.copy()
        bindings = []
        for node in self.scope_nodes(body):
            if isinstance(node, ast.Assign):
                bindings.append((node.targets, node.value))
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)) and node.value is not None:
                bindings.append(([node.target], node.value))
        previous = None
        while previous != local:
            previous = local.copy()
            for targets, value in bindings:
                if self.path_condition(value, local):
                    for target in targets:
                        for name in ast.walk(target):
                            if isinstance(name, ast.Name) and isinstance(name.ctx, ast.Store):
                                local[name.id] = frozenset({'@path'})
        return local

    def narrowed(self, body, env, guarded=False):
        for node in body:
            if isinstance(node, (ast.If, ast.While)):
                if (self.path_condition(node.test, env) or self.narrowed(node.body, env, True)
                        or self.narrowed(node.orelse, env, True)):
                    return True
            elif isinstance(node, ast.Match):
                if self.path_condition(node.subject, env):
                    return True
                for case in node.cases:
                    if (case.guard is not None and self.path_condition(case.guard, env)
                            or self.narrowed(case.body, env, True)):
                        return True
            elif isinstance(node, ast.Try):
                transfers = any(isinstance(part, (ast.Continue, ast.Break))
                                for handler in node.handlers for part in self.scope_nodes(handler.body, classes=True))
                path = any(isinstance(part, ast.Name) and self.path_condition(part, env)
                           for part in self.scope_nodes(node.body, classes=True))
                if (transfers and path or self.narrowed(node.body + node.orelse + node.finalbody, env, guarded)
                        or any(self.narrowed(handler.body, env, True) for handler in node.handlers)):
                    return True
            elif (isinstance(node, ast.Break) and not guarded
                  or isinstance(node, (ast.Raise, ast.Return, ast.Yield, ast.YieldFrom))):
                return True
            elif isinstance(node, ast.ClassDef):
                if self.narrowed(node.body, self.path_aliases(node.body, env), guarded):
                    return True
            elif not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                if self.narrowed(ast.iter_child_nodes(node), env, guarded):
                    return True
        return False

    def aliases(self, body, env):
        pending = {}
        for statement in body:
            if isinstance(statement, ast.Assign):
                for target in statement.targets:
                    if isinstance(target, ast.Name):
                        pending[target.id] = statement.value
        while pending:
            ready = [name for name, value in pending.items()
                     if not {p.id for p in ast.walk(value) if isinstance(p, ast.Name)} & pending.keys()]
            if not ready:
                break
            for name in ready:
                env[name] = self.value(pending.pop(name), env)

    @staticmethod
    def scope_nodes(body, classes=False):
        for node in body:
            yield node
            if (not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
                    and (classes or not isinstance(node, ast.ClassDef))):
                yield from _Enumeration.scope_nodes(ast.iter_child_nodes(node), classes)

    def block(self, body, env, exempt):
        for statement in body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                local = env.copy()
                for p in self.scope_nodes(statement.body):
                    if isinstance(p, ast.Name) and isinstance(p.ctx, ast.Store):
                        local[p.id] = None
                for arg in ast.walk(statement.args):
                    if isinstance(arg, ast.arg):
                        local[arg.arg] = None
                self.aliases(statement.body, local)
                self.block(statement.body, local, exempt)
            elif isinstance(statement, ast.ClassDef):
                for child in statement.body:
                    self.block([child], env.copy(), exempt)
            elif isinstance(statement, ast.Assign):
                value = self.value(statement.value, env)
                for target in statement.targets:
                    self.bind(target, value, env)
            elif isinstance(statement, ast.AugAssign):
                value = self.value(statement.target, env)
                extra = self.collection(self.value(statement.value, env)) if isinstance(statement.op, ast.Add) else None
                self.bind(statement.target, self.merge(value, extra), env)
            elif isinstance(statement, ast.For):
                local = env.copy()
                self.bind(statement.target, self.element(self.value(statement.iter, env)), local)
                self.aliases(statement.body, local)
                narrowing = self.narrowed(statement.body, self.path_aliases(statement.body, local))
                if narrowing:
                    self.bind(statement.target, None, local)
                    self.aliases(statement.body, local)
                self.block(statement.body, local, set() if narrowing else exempt)
                # Only collections accumulated in the enclosing scope escape the loop.
                for name, before in list(env.items()):
                    if before == 'empty' or isinstance(before, tuple) and before[0] == 'collection':
                        env[name] = None if narrowing and local.get(name) != before else self.merge(before, local.get(name))
            elif isinstance(statement, ast.If):
                yes, no = env.copy(), env.copy()
                self.block(statement.body, yes, exempt)
                self.block(statement.orelse, no, exempt)
                for name in yes.keys() | no.keys():
                    env[name] = self.merge(yes.get(name), no.get(name))
            elif isinstance(statement, ast.With):
                self.block(statement.body, env, exempt)
            elif (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)
                  and isinstance(statement.value.func, ast.Attribute)
                  and statement.value.func.attr in ('append', 'extend') and len(statement.value.args) == 1):
                call = statement.value
                current = self.value(call.func.value, env)
                extra = self.value(call.args[0], env)
                if call.func.attr == 'append':
                    extra = ('collection', extra)
                else:
                    extra = self.collection(extra)
                self.bind(call.func.value, self.merge(current, extra), env)
            elif (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)
                  and isinstance(statement.value.func, ast.Attribute)):
                self.bind(statement.value.func.value, None, env)
            test = _invariant_test(statement)
            keys = self.value(test.left, env) if isinstance(test, ast.Compare) else None
            if not (isinstance(test, ast.Compare) and len(test.ops) == 1
                    and isinstance(test.ops[0], (ast.Eq, ast.Is, ast.In))
                    and isinstance(keys, frozenset) and 'with' in keys and '@path' not in keys):
                continue
            value = test.comparators[0]
            values = value.elts if isinstance(test.ops[0], ast.In) and isinstance(value, (ast.Tuple, ast.List, ast.Set)) else [value]
            false = bool(values) and all(isinstance(p, ast.Constant) and (p.value is False or p.value == 'false') for p in values)
            if ('persist-credentials' in keys and false
                    or 'run-id' in keys and isinstance(value, ast.Constant) and isinstance(value.value, str)
                    and ''.join(value.value.split()) == '${{github.event.workflow_run.id}}'
                    and isinstance(test.ops[0], (ast.Eq, ast.Is))):
                exempt.add(statement.lineno)


def _enumerated_invariants(rows):
    """Security-input assertions with enumeration provenance in parseable Python hunks."""
    chunks, chunk = [], []
    for path, line, kind, text in rows:
        if kind == '@':
            if chunk:
                chunks.append(chunk)
            chunk = []
        elif kind != '-':
            chunk.append((path, line, text))
    if chunk:
        chunks.append(chunk)
    parsed, assignments = [], {}
    for chunk in chunks:
        # A PR hunk may begin in the previous function, before a complete definition.
        starts = [0] + [i for i, (_, _, text) in enumerate(chunk) if i and text and not text[0].isspace()]
        for offset in starts:
            try:
                tree = _parse(textwrap.dedent('\n'.join(text for _, _, text in chunk[offset:])))
            except SyntaxError:
                continue
            path, start, _ = chunk[offset]
            parsed.append((path, start, tree))
            for statement in tree.body:
                if isinstance(statement, ast.Assign) and not chunk[offset + statement.lineno - 1][2][:1].isspace():
                    for target in statement.targets:
                        if isinstance(target, ast.Name):
                            assignments.setdefault(path, []).append(statement)
            break
    resolver, modules = _Enumeration(), {}
    for path, body in assignments.items():
        env = modules.setdefault(path, {})
        resolver.aliases(body, env)
    exempt = set()
    for path, start, tree in parsed:
        env = modules.get(path, {}).copy()
        found = set()
        resolver.block(tree.body, env, found)
        exempt.update((path, start + line - 1) for line in found)
    return exempt


def _python_assertion(text, bindings):
    """Return a proven Python verdict, or None to retain the base text checks."""
    try:
        statement = _parse(text).body[0]
    except (SyntaxError, IndexError):
        return None
    if isinstance(statement, ast.Assert):
        test = statement.test  # assertion messages are not operands
        if not _VERSION_REF.search(text) and _step_order(test, bindings):
            return False
        if not isinstance(test, ast.Compare):
            if not _VERSION_REF.search(text) and (
                    not _field(test, bindings) or isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not)
                    and (isinstance(test.operand, (ast.ListComp, ast.GeneratorExp))
                         or isinstance(test.operand, ast.Call) and isinstance(test.operand.func, ast.Name)
                         and test.operand.func.id == 'any')):
                return False
            return None  # an unrecognized assertion still goes through the base text checks
        operands = [test.left, *test.comparators]
        if (len(test.ops) == 1 and isinstance(test.ops[0], ast.In)
                and isinstance(test.left, ast.Constant) and isinstance(test.left.value, str)
                and re.fullmatch(r'(?:persist-credentials|fetch-depth|run-id):\s*\S.*', test.left.value)):
            return True
        if not _VERSION_REF.search(text) and any(isinstance(op, (ast.NotIn, ast.IsNot)) for op in test.ops):
            return False
    elif (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)
          and isinstance(statement.value.func, ast.Attribute)
          and statement.value.func.attr.startswith('assert')):
        call = statement.value
        if (call.func.attr in ('assertFalse', 'assertNotIn', 'assertIsNone', 'assertIsNotNone')
                and not _VERSION_REF.search(text)):
            return False
        operands = call.args[:2]
    else:
        return None
    # A classifier given a literal action dictionary is testing fixture data.
    if any(isinstance(p, ast.Call) and any(
            isinstance(arg, ast.Dict) and any(isinstance(key, ast.Constant) and key.value == 'uses'
                                             for key in arg.keys) for arg in p.args)
           and all(_literal(arg, bindings) for arg in p.args) for p in operands
           ) and not any(_field(operand, bindings) for operand in operands):
        return False
    if (len(operands) == 2 and isinstance(operands[0], ast.JoinedStr)
            and isinstance(operands[1], ast.Attribute) and operands[1].attr == 'stdout'):
        return False  # pin-sync output over fixture files
    fields, exempted, consistency = 0, 0, False
    for operand in operands:
        field = _field(operand, bindings)
        if not field:
            continue
        fields += 1
        if _fixture_operand(operand, bindings):
            exempted += 1
            continue
        # Removing the revision before comparing action identities is a security/structure check.
        if field == 'uses' and _identity(operand):
            exempted += 1
            continue
        for other in operands:
            if other is operand:
                continue
            if not _literal(other, bindings):
                if not _VERSION_REF.search(ast.unparse(other)):
                    consistency |= bool(_field(other, bindings) or isinstance(other, (ast.JoinedStr, ast.BinOp))
                                        and _derived(other, bindings)
                                        # a string composed around the input, set against another step's read value
                                        or isinstance(operand, ast.JoinedStr) and isinstance(other, ast.Subscript)
                                        and isinstance(other.value, ast.Call)
                                        # a value a call derives from a different step's field
                                        or bool(_step_fields(other, bindings))
                                        and _root(operand) not in _step_fields(other, bindings)
                                        or isinstance(operand, (ast.SetComp, ast.ListComp))
                                        and isinstance(other, (ast.SetComp, ast.ListComp, ast.Name))
                                        or isinstance(statement, ast.Assert) and isinstance(statement.test, ast.Compare)
                                        and any(isinstance(op, ast.In) for op in statement.test.ops)
                                        and isinstance(other, ast.Name))
                continue
            return True
    if fields and (fields == exempted or consistency):
        return False
    return None


def _asserts_on_step(text, bindings):
    """An assertion statement reading a step's `uses` or `with`, minus the shapes the rule keeps."""
    python = _python_assertion(text, bindings)
    if python is not None:
        return python
    if _ASSERTION.search(text) and _VERSION_REF.search(text):
        return True
    if not (_KEY.search(text) and _ASSERTION.search(text)):
        return False
    if (_ABSENCE.search(text)
            or re.search(r'\.some\(', text) and re.search(r'\.toBe\(false\)', text)
            and not _WITH_KEY.search(text) and r'actions\/checkout' in text):
        return False  # absence checks stay
    if len(_WITH_KEY.findall(text)) >= 2 or (_WITH_KEY.search(text) and _OTHER_STEP.search(text)):
        return False  # a consistency check: one step's with against another value from the workflows
    # Only a comparison with a literal pins a value; one against a value computed from the
    # workflows (`_shipped_action_pin(...)`) is a consistency check the rule keeps.
    # Only the cited workflow step-output template proves a cross-step comparison.
    text = re.sub(r'`\\?\$\{\{ steps\.\$\{[\w.]+\}\.outputs\.\w+ \}\}`', 'COMPUTED', text)
    if re.search(r'\.uses!?\.replace\(/@\[0-9a-f\]\{40\}\$/', text):
        return False
    bare = _SUBSCRIPT.sub('', text)
    for _ in range(4):  # a value computed by a call (innermost first) is not a literal
        bare = _CALL.sub('CALL', bare)
    return bool(_LITERAL.search(bare))


def _statements(rows):
    """Group added statements up to _SPAN_MAX lines; emit None text at scope/hunk boundaries."""
    out, current, depth = [], None, 0
    for path, line, kind, text in rows:
        if kind != '+' or not is_test_file(path):
            if current:
                out.append(current)
            if is_test_file(path) and (kind == '@' or kind == ' ' and re.match(r'\s*(?:async\s+)?def\b|\s*class\b', text)):
                out.append([path, line, None])
            current, depth = None, 0
            continue
        starts_assertion = re.match(r'\s*(?:assert\s|self\.assert\w*\(|expect\s*\()', text)
        if (current and current[0] == path and depth > 0 and (len(current[2]) < _SPAN_MAX
                    or re.fullmatch(r'\s*(?:"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')\s*\)?\s*', text))
                and not starts_assertion):
            current[2].append(text.strip())
        else:
            if current:
                out.append(current)
            current, depth = [path, line, [text.strip()]], 0
            if not (_ASSERTION.search(text) or re.match(r'\s*\w+\s*=', text)):
                out.append(current)
                current = None
                continue
        code = _STRING.sub('STRING', text)
        depth += code.count('(') + code.count('[') + code.count('{') \
            - code.count(')') - code.count(']') - code.count('}')
        if depth <= 0:
            out.append(current)
            current, depth = None, 0
    if current:
        out.append(current)
    return [(p, n, None if parts is None else ' '.join(parts)) for p, n, parts in out]


def _without_message(text):
    """Keep Python/expect assertion operands and discard their diagnostic arguments."""
    try:
        statement = _parse(text).body[0]
    except (SyntaxError, IndexError):
        statement = None
    if isinstance(statement, ast.Assert):
        return 'assert ' + ast.unparse(statement.test)
    if (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)
            and isinstance(statement.value.func, ast.Attribute)
            and statement.value.func.attr.startswith('assert')):
        call = statement.value
        count = 1 if call.func.attr in ('assertTrue', 'assertFalse', 'assertIsNone', 'assertIsNotNone') else 2
        call.args = call.args[:count]
        call.keywords = [arg for arg in call.keywords if arg.arg != 'msg']
        return ast.unparse(call)
    code = _STRING.sub(lambda m: ' ' * len(m.group(0)), text)
    start = re.search(r'\bexpect\s*\(', code)
    if not start:
        return text
    depth, comma = 1, None
    for i in range(start.end(), len(code)):
        if code[i] in '([{':
            depth += 1
        elif code[i] in ')]}':
            depth -= 1
            if depth == 0:
                return text if comma is None else text[:comma] + text[i:]
        elif code[i] == ',' and depth == 1 and comma is None:
            comma = i
    return text


def _bind(text, bindings):
    """Track local fixture values, computed pins and input aliases within the visible diff."""
    if re.match(r'(?:async\s+)?def\b|class\b', text):
        bindings.clear()
    try:
        statement = _parse(text + ('\n pass' if text.endswith(':') else '')).body[0]
    except SyntaxError:
        bindings.clear()
        return
    except IndexError:
        return
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
        bindings.update({p.arg: None for p in ast.walk(statement.args) if isinstance(p, ast.arg)})
    stores = {p.id for p in ast.walk(statement) if isinstance(p, ast.Name) and isinstance(p.ctx, ast.Store)}
    if not isinstance(statement, ast.Assign):
        for name in stores:
            bindings.pop(name, None)
            bindings.pop(('step-fields', name), None)
        return
    value = statement.value
    fields = _step_fields(value, bindings)
    if _pin_pattern(value, bindings):
        kind = 'pin-pattern'
    elif _field(value, bindings) == 'with':
        kind = 'with'
    elif any(isinstance(p, ast.Call) and isinstance(p.func, ast.Attribute)
             and p.func.attr in ('workflow_steps', 'workflow_jobs') and p.args
             and isinstance(p.args[0], ast.Name) and bindings.get(p.args[0].id) == 'literal'
             for p in ast.walk(value)) or _fixture_operand(value, bindings):
        kind = 'fixture'
    elif _derived(value, bindings):
        kind = 'derived'
    elif _step_value(value, bindings):
        kind = 'step'
    elif isinstance(value, ast.Constant) and isinstance(value.value, str):
        kind = 'literal'
    else:
        kind = None
    for name in stores:
        bindings.pop(name, None)
        bindings.pop(('step-fields', name), None)
    for target in statement.targets:
        if isinstance(target, ast.Name):
            bindings[target.id] = kind
            if fields:  # beside the kind: a value derived from a step's field may also be 'derived'
                bindings['step-fields', target.id] = frozenset(fields)


class CannotScreen(Exception):
    """A pin source that cannot be parsed: it is named, never read as holding no pins."""


def source_kind(path):
    """Which pin source a repository path is, or None."""
    name = path.rsplit('/', 1)[-1]
    if path.startswith('.github/workflows/') and name.endswith(('.yml', '.yaml')):
        return 'workflow'
    if name in ('Dockerfile', 'Containerfile') or name.endswith('.Dockerfile'):
        return 'dockerfile'
    if re.fullmatch(r'requirements[\w.-]*\.txt', name):
        return 'requirements'
    if name in ('package.json', '.tool-versions', '.python-version', '.nvmrc'):
        return name
    return None


def _path_parts(node, bindings=None):
    if bindings is not None and isinstance(node, ast.Name):
        return bindings.get(node.id, [])
    if (bindings is not None and isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == 'Path' and len(node.args) == 1):
        return _path_parts(node.args[0], bindings)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _path_parts(node.left, bindings) + _path_parts(node.right, bindings)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    return []


def _callee(call):
    return call.func.id if isinstance(call.func, ast.Name) else call.func.attr if isinstance(call.func, ast.Attribute) else None


def _file_name(node):
    """The last component of a path expression: a literal, a `/` join, os.path.join(...) or Path(...)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.replace('\\', '/').rsplit('/', 1)[-1]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _file_name(node.right)
    if isinstance(node, ast.Call) and node.args and (_callee(node) == 'join' or _callee(node) in ('Path', 'str')
                                                     and len(node.args) == 1):
        return _file_name(node.args[-1])
    return None


def _loads_screen(tree):
    """Whether a module's code (not its comments or strings) loads this screen itself."""
    return any(isinstance(p, ast.Import) and any(a.name == 'check_pin_tests' for a in p.names)
               or isinstance(p, ast.ImportFrom) and not p.level and p.module == 'check_pin_tests'
               or isinstance(p, ast.Call) and _callee(p) == 'spec_from_file_location' and len(p.args) >= 2
               and isinstance(p.args[0], ast.Constant) and p.args[0].value == 'check_pin_tests'
               and _file_name(p.args[1]) == 'check_pin_tests.py'
               for p in ast.walk(tree))


_PATH_CALLS = ('open', 'Path', 'join', 'glob', 'iglob', 'rglob', 'listdir', 'readFileSync', 'readFile',
               'readdirSync', 'readdir')


def _names_source_in_code(tree, kind):
    """Whether code outside string literals names the pin source in a `/` join or a path/reader call."""
    for p in ast.walk(tree):
        if isinstance(p, ast.BinOp) and isinstance(p.op, ast.Div):
            path = '/'.join(_path_parts(p))
        elif isinstance(p, ast.Call) and _callee(p) in _PATH_CALLS:
            path = '/'.join(a.value for a in p.args if isinstance(a, ast.Constant) and isinstance(a.value, str))
        else:
            continue
        if _SOURCES[kind].search(path):
            return True
    return False


def couples(text, kind):
    """Whether a test file's text reads the pin source `kind`: the one coupling predicate."""
    if not _READS.search(text):
        return False
    tree = None
    if 'check_pin_tests' in text or not _SOURCES[kind].search(text) and kind in ('workflow', 'pin-shape'):
        try:
            tree = _parse(text)
        except SyntaxError:
            pass
    # The screen's own suite: its fixture text is input to the screen, not a reading of the repository.
    if tree is not None and _loads_screen(tree) and not _names_source_in_code(tree, kind):
        return False
    if _SOURCES[kind].search(text):
        return True
    if tree is None or kind not in ('workflow', 'pin-shape'):
        return False
    return any(isinstance(p, ast.BinOp) and '/'.join(_path_parts(p)) == '.github/workflows'
               for p in ast.walk(tree))


def dockerfile_pins(text):
    """The literals the FROM lines pin (see the module docstring)."""
    args, pins = {}, set()
    for line in re.sub(r'\\\r?\n', ' ', text).splitlines():
        words = line.split()
        if not words:
            continue
        if words[0].upper() == 'ARG':
            for word in words[1:]:
                name, eq, value = word.partition('=')
                if eq:
                    args[name] = value.strip('"\'')
            continue
        rest = [w for w in words[1:] if not w.startswith('--')]
        if words[0].upper() != 'FROM' or not rest:
            continue
        unresolved = []
        ref = _ARG_REF.sub(lambda m: args.get(m.group(1) or m.group(3), m.group(2))
                           or unresolved.append(m.group(0)) or '', rest[0])
        if unresolved:
            continue
        # A stage alias (FROM deps) and scratch carry no tag or digest, so they add nothing here.
        ref, _, digest = ref.partition('@')
        name, colon, tag = ref.rsplit('/', 1)[-1].partition(':')
        if colon and re.search(r'\d', tag):
            pins.add(f'{name}:{tag}')
        if colon and _DOTTED.search(tag):
            pins.add(tag)
        if re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
            pins.add(digest[len('sha256:'):])
    return pins


def manifest_pins(kind, text):
    """The pinned literals in one pin source's text; CannotScreen when it does not parse."""
    if kind == 'dockerfile':
        return dockerfile_pins(text)
    lines = [re.sub(r'(^|\s)#.*', '', line) for line in re.sub(r'\\\r?\n', ' ', text).splitlines()]
    pins = set()
    if kind == 'requirements':
        for line in lines:
            m = re.match(r'\s*[A-Za-z0-9][\w.-]*(?:\[[^\]]*\])?\s*===?\s*([^\s;,]+)', line)
            if m and _DOTTED.search(m.group(1)):
                pins.add(m.group(1))
    elif kind == 'package.json':
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise CannotScreen(f'not valid JSON ({exc})') from exc
        if not isinstance(data, dict):
            raise CannotScreen('not a JSON object')
        for key in _DEP_KEYS:
            section = data.get(key)
            for value in (section.values() if isinstance(section, dict) else ()):
                m = _EXACT.fullmatch(value.strip()) if isinstance(value, str) else None
                if m:
                    pins.add(m.group(1))
        m = re.fullmatch(r'[^@\s]+@(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)(?:\+.*)?', str(data.get('packageManager', '')))
        if m:
            pins.add(m.group(1))
    elif kind == 'workflow':
        for line in lines:
            found = []
            if re.search(r'\bpip3?\s+install\b', line):
                found += _PIP_PIN.findall(line)
            if re.search(r'\bnpm\s+(?:i|install|add)\b', line):
                found += _NPM_PIN.findall(line)
            pins.update(v for v in found if _DOTTED.search(v))
    else:  # .tool-versions, .python-version, .nvmrc: each word that starts with a dotted version
        for line in lines:  # a tool name or a `ref:`/`path:` word never starts with a digit
            pins.update(w.removeprefix('v') for w in line.split() if _DOTTED.match(w.removeprefix('v')))
    return pins


def _quoted(text):
    """The quoted strings a statement holds: a comment line or bare prose holds none."""
    return [] if _COMMENT.match(text) else [m.group(0) for m in _STRING.finditer(text)]


def _matcher(literals):
    """One pattern holding any of the literals whole: 1.2.3 is not held by 11.2.3, 1.2.34 or 1.2.3.4."""
    alternatives = '|'.join(re.escape(lit) for lit in sorted(literals, key=len, reverse=True))
    return re.compile(r'(?<![\w.])v?(?:' + alternatives + r')(?!\w|\.\d)')


def findings(diff_text, pinned=(), reads_workflows=None, literals=None, reads_source=None):
    """Candidate (path, line, text) statements a diff adds to tests that pin a dependency.

    pinned: SHAs the workflows pin. literals: {source kind: pinned literals}
    (see manifest_pins). Both are widened with the pins the diff's own lines
    in those sources carry. reads_source(path, kind) says whether that test
    file reads the pin source; without it, reads_workflows(path) answers for
    the workflows and the file's lines in the diff answer the rest. A pin
    counts only in a file that reads its source, so fixture data in a parser's
    tests is not flagged. These are candidates for a reader."""
    rows = list(_lines(diff_text))
    pins = set(pinned)
    held = {kind: set(values) for kind, values in (literals or {}).items()}
    texts = {}
    for path, _, kind, text in rows:
        if kind == '-':
            continue
        if path.startswith('.github/workflows/'):
            pins.update(_ACTION_PIN.findall(text))
        texts.setdefault(path, []).append(text)
    for path, lines in texts.items():
        kind = source_kind(path)
        if kind:
            try:
                held.setdefault(kind, set()).update(manifest_pins(kind, '\n'.join(lines)))
            except CannotScreen:
                pass  # a hunk of package.json is no whole document; main reads the head file whole
    if reads_source is None:
        def reads_source(path, kind):
            if kind == 'workflow' and reads_workflows:
                return reads_workflows(path)
            return couples('\n'.join(texts.get(path, ())), kind)
    coupled = {}  # (path, kind) -> bool: each file is judged once per source, not once per statement
    matchers = {kind: _matcher(values) for kind, values in held.items() if values}
    out = []
    bindings = {}
    invariants = _enumerated_invariants(rows)
    statements = _statements(rows)
    top_level = {(p, n) for p, n, k, t in rows if k == '+' and t and not t[0].isspace()}
    module_bindings = {}
    for path, line, text in statements:
        if text is not None and (path, line) in top_level and re.match(r'\w+\s*=', text):
            names = {}
            _bind(text, names)
            module_bindings.setdefault(path, {}).update({k: v for k, v in names.items() if v in ('pin-pattern', 'derived')})
    for path, line, text in statements:
        if text is None:
            bindings.clear()
            continue
        for name, kind in module_bindings.get(path, {}).items():
            if kind == 'derived':
                bindings.setdefault(name, kind)
        _bind(text, bindings)
        subject = _without_message(text)
        pattern_bindings = {**module_bindings.get(path, {}), **bindings}
        for kind in ('workflow', 'pin-shape', *matchers):
            if (path, kind) not in coupled:
                coupled[path, kind] = bool(reads_source(path, kind))
        on_step = coupled[path, 'workflow'] and (
            _asserts_on_step(subject, bindings) or any(sha in pins for sha in _SHA.findall(subject)))
        if (path, line) in invariants:
            on_step = False
        if on_step or coupled[path, 'pin-shape'] and _pin_shape(subject, pattern_bindings) or any(coupled[path, kind] and any(m.search(q) for q in _quoted(text))
                          for kind, m in matchers.items()):
            out.append((path, line, text))
    return out


def _gh(*args):
    return subprocess.run(['gh', *args], capture_output=True, text=True, encoding='utf-8',
                          errors='replace', check=True).stdout


def head_sources(repo, sha, unscreened):
    """(SHAs the workflows pin, {source kind: pinned literals}) from every pin source in the head's tree.

    A tree GitHub truncated, or a source it cannot fetch or parse, is named on
    stderr and appended to unscreened."""
    tree = json.loads(_gh('api', f'repos/{repo}/git/trees/{sha}?recursive=1'))
    if tree.get('truncated'):
        _cannot_screen(unscreened, f'the tree at {sha}', 'GitHub truncated the recursive listing')
    pins, held = set(), {}
    for entry in tree.get('tree', []):
        path = entry.get('path', '')
        kind = source_kind(path) if entry.get('type') == 'blob' else None
        if not kind:
            continue
        try:
            blob = json.loads(_gh('api', f'repos/{repo}/git/blobs/{entry["sha"]}'))
            text = base64.b64decode(blob['content']).decode('utf-8', 'replace')
            held.setdefault(kind, set()).update(manifest_pins(kind, text))
        except subprocess.CalledProcessError as exc:
            _cannot_screen(unscreened, path, (exc.stderr or str(exc)).strip())
            continue
        except (CannotScreen, KeyError, ValueError) as exc:
            _cannot_screen(unscreened, path, str(exc))
            continue
        if kind == 'workflow':
            pins.update(_ACTION_PIN.findall(text))
    return pins, held


def _cannot_screen(unscreened, what, why):
    unscreened.append(what)
    print(f'could not screen {what}: {why}', file=sys.stderr)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--repo', required=True)
    ap.add_argument('--pr', required=True, type=int)
    args = ap.parse_args(argv)
    try:
        return _screen(args)
    except Exception as exc:  # an uncaught exception exits 1, the candidates code: a crash is "could not screen"
        print(f'could not screen {args.repo} PR {args.pr}: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 2


def _screen(args):
    unscreened = []
    try:
        head = json.loads(_gh('pr', 'view', str(args.pr), '-R', args.repo, '--json', 'headRefOid'))['headRefOid']
        diff_text = _gh('pr', 'diff', str(args.pr), '-R', args.repo)
        pins, held = head_sources(args.repo, head, unscreened)
    except (subprocess.CalledProcessError, KeyError, ValueError) as exc:
        detail = getattr(exc, 'stderr', '') or str(exc)
        print(f'check_pin_tests: cannot read {args.repo} PR {args.pr}: {detail.strip()}', file=sys.stderr)
        return 2
    cache = {}

    def reads_source(path, kind):
        if path not in cache:
            try:
                cache[path] = _gh('api', f'repos/{args.repo}/contents/{path}?ref={head}', '-H',
                                  'Accept: application/vnd.github.raw')
            except subprocess.CalledProcessError as exc:
                cache[path] = None
                _cannot_screen(unscreened, path, (exc.stderr or str(exc)).strip())
        return cache[path] is not None and couples(cache[path], kind)

    context = []
    for path in dict.fromkeys(p for p, _, k, _ in _lines(diff_text) if k == '+' and is_test_file(p)):
        reads_source(path, 'workflow')
        if cache[path] is not None:
            lines = cache[path].splitlines()
            context.append(f'+++ b/{path}\n@@ -1,{len(lines)} +1,{len(lines)} @@\n'
                           + ''.join(f' {line}\n' for line in lines))
    found = findings(''.join(context) + diff_text, pins, None, held, reads_source)
    for path, line, text in found:
        print(f'{path}:{line}: {text}')
    blind = 'misses every pin it cannot read as a literal from a source it reads (see --help)'
    if found:
        print(f'{len(found)} candidate test line(s) holding or asserting a pinned workflow action or input, '
              "Dockerfile base image or tool version: judge each against the repository's own rule — no test "
              'asserts on a pinned version, tag, SHA or digest of any dependency: a workflow `uses:` value (the '
              "action's SHA, release, version comment or pin shape) or its `with:` inputs, a Dockerfile `FROM` "
              'base image, a tool version or a package version, and a legitimate bump must never fail a test — '
              f'and delete those that pin a dependency; this screen {blind}, so it clears nothing it does not list.',
              file=sys.stderr)
        return 1
    if unscreened:
        print(f'{len(unscreened)} file(s) could not be screened (above); no candidates among the rest proves '
              'nothing about them, and an empty list is not a proof.', file=sys.stderr)
        return 2
    print('no candidate test lines among the workflow, Dockerfile base-image and tool-version pins this screen '
          f'reads; it {blind}, so an empty list is not a proof.', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
