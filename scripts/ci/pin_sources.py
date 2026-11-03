"""Pin-source readers; check_pin_tests.py documents each source."""
import json
import re


_DOTTED = re.compile(r'\d+\.\d+')
_ARG_REF = re.compile(r'\$\{(\w+)(?::-([^}]*))?\}|\$(\w+)')
_DEP_KEYS = ('dependencies', 'devDependencies', 'peerDependencies', 'optionalDependencies', 'engines')
_EXACT = re.compile(r'[v=]?(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?)')
_PIP_PIN = re.compile(r'''(?<![\w.-])[A-Za-z0-9][\w.-]*(?:\[[^\]]*\])?===?([^\s'"`;,)]+)''')
_NPM_PIN = re.compile(r'(?<![\w@/.-])@?[\w.-]+(?:/[\w.-]+)?@(\d+\.\d+[\w.+-]*)')
_YAML_LINE = re.compile(r'(\s*)(-\s+)?(?:([\w-]+):(?:\s+|$))?(.*)')
_FLOW_ITEM = re.compile(r'''(?:^|[\[{,])\s*(?:([\w-]+):\s+)?["']?([^\s,\[\]{}"']+)''')
_ITEM_KEY = {'image': 'name', 'services': 'name', 'include': 'local'}


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
    if path in ('.gitlab-ci.yml', '.gitlab-ci.yaml'):
        return 'gitlab-ci'
    return None


def image_pins(ref):
    """The literals an image reference pins by the FROM rules."""
    pins = set()
    ref, _, digest = ref.partition('@')
    name, colon, tag = ref.rsplit('/', 1)[-1].partition(':')
    if colon and re.search(r'\d', tag):
        pins.add(f'{name}:{tag}')
    if colon and _DOTTED.search(tag):
        pins.add(tag)
    if re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
        pins.add(digest[len('sha256:'):])
    return pins


def _scalars(value, key):
    """A value's scalars: itself, or a flow collection's plain and `key:` items."""
    if value[:1] not in ('[', '{'):
        return [value.strip('"\'')] if value else []
    return [v for k, v in _FLOW_ITEM.findall(value) if k in ('', key)]


def gitlab_ci(text):
    """(image references, local include patterns) a GitLab CI file names."""
    found = {key: [] for key in _ITEM_KEY}
    block = []  # [key indent, key, item indent]
    for raw in text.splitlines():
        line = re.sub(r'(^|\s)#.*', '', raw).rstrip()
        m = _YAML_LINE.match(line)
        assert m is not None
        indent, dash, key, value = m.groups()
        at = len(indent)
        if not line.strip():
            continue
        if block and (at > block[0] or at == block[0] and dash):
            if dash and block[2] < 0:
                block[2] = at
            if key == _ITEM_KEY[block[1]] or dash and not key and at == block[2]:
                found[block[1]] += _scalars(value, _ITEM_KEY[block[1]])
            continue
        block = [at, key, -1] if key in _ITEM_KEY and not value else []
        if key in _ITEM_KEY:
            found[key] += _scalars(value, _ITEM_KEY[key])
    return ([r for r in found['image'] + found['services'] if '$' not in r],
            [p.lstrip('/') for p in found['include'] if not re.match(r'\w+://', p)])


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
        ref = _ARG_REF.sub(lambda m, args=args, unresolved=unresolved:
                           args.get(m.group(1) or m.group(3), m.group(2))
                           or unresolved.append(m.group(0)) or '', rest[0])
        if unresolved:
            continue
        # A stage alias (FROM deps) and scratch carry no tag or digest, so they add nothing here.
        pins |= image_pins(ref)
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
    elif kind in ('workflow', 'gitlab-ci'):
        for line in lines:
            found = []
            if re.search(r'\bpip3?\s+install\b', line):
                found += _PIP_PIN.findall(line)
            if re.search(r'\bnpm\s+(?:i|install|add)\b', line):
                found += _NPM_PIN.findall(line)
            pins.update(v for v in found if _DOTTED.search(v))
        for ref in gitlab_ci(text)[0] if kind == 'gitlab-ci' else ():
            pins |= image_pins(ref)
    else:  # .tool-versions, .python-version, .nvmrc: each word that starts with a dotted version
        for line in lines:  # a tool name or a `ref:`/`path:` word never starts with a digit
            pins.update(w.removeprefix('v') for w in line.split() if _DOTTED.match(w.removeprefix('v')))
    return pins
