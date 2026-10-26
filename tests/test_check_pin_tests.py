"""check_pin_tests.findings: test lines a PR adds that assert on a workflow pin or its inputs."""
import importlib.util
import os
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location('check_pin_tests', os.path.join(KIT, 'check_pin_tests.py'))
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

SHA = '721426819be9e05deb76d56364f671b87fa05fbf'


def diff(path, *lines, removed=()):
    rows = [f'diff --git a/{path} b/{path}', f'--- a/{path}', f'+++ b/{path}', '@@ -1,1 +1,9 @@']
    rows += [f'-{line}' for line in removed]
    rows += [f'+{line}' for line in lines]
    return '\n'.join(rows) + '\n'


class ParseWarnings(unittest.TestCase):
    def test_a_scanned_files_invalid_escape_prints_no_syntax_warning(self):
        import warnings
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter('always')
            tree = mod._parse('x = "\\$HOME"\n')
        self.assertEqual([w for w in seen if issubclass(w.category, SyntaxWarning)], [])
        self.assertEqual(tree.body[0].value.value, '\\$HOME')


class Findings(unittest.TestCase):
    def flagged(self, text, pinned=(), coupled=True):
        reads = (lambda path: coupled)
        return [(f, line) for f, _, line in mod.findings(text, pinned, reads)]

    def test_a_constant_holding_a_workflow_pin_is_flagged(self):
        got = self.flagged(diff('tests/test_comment_share_workflow.py', f'PIN = "{SHA}"'), {SHA})
        self.assertEqual(got, [('tests/test_comment_share_workflow.py', f'PIN = "{SHA}"')])

    def test_a_pin_the_same_diff_adds_to_a_workflow_counts_where_the_test_reads_workflows(self):
        text = (diff('.github/workflows/c.yml', f'      - uses: Nitjsefnie-Actions/comment-ratchet@{SHA} # v1.0.0')
                + diff('tests/test_wf.py', 'WF = (ROOT / ".github/workflows/c.yml").read_text()', f'PIN = "{SHA}"'))
        self.assertEqual([t for _, _, t in mod.findings(text)], [f'PIN = "{SHA}"'])

    def test_a_workflow_path_in_fixture_data_does_not_couple_the_file(self):
        text = (diff('.github/workflows/c.yml', f'      - uses: o/claim@{SHA}')
                + diff('tests/domain/claim-path.test.ts', 'const wf = { path: ".github/workflows/claim.yml" };',
                       f'const action = "o/claim@{SHA}";'))
        self.assertEqual(mod.findings(text), [])

    def test_a_pinned_sha_in_a_parser_fixture_is_not_flagged(self):
        line = f'const sha = "{SHA}";'
        self.assertEqual(self.flagged(diff('tests/domain/claim-path.test.ts', line), {SHA}, coupled=False), [])

    def test_version_references_are_flagged_where_the_test_reads_workflows(self):
        for line in ('assert "comment-ratchet@v1" in wf_text', 'assert "# v1.0.0" in wf_text',
                     'grep -q "claim@v2.0.5" .github/workflows/claim.yml'):
            self.assertEqual(len(self.flagged(diff('tests/test_wf.py', line))), 1, line)
            self.assertEqual(self.flagged(diff('tests/test_wf.py', line), coupled=False), [], line)

    def test_get_access_is_flagged(self):
        for line in ('assert step.get("uses") == "actions/checkout@v4"',
                     'assert step.get("with", {}).get("fetch-depth") == 2'):
            self.assertEqual(len(self.flagged(diff('tests/test_wf.py', line))), 1, line)

    def test_a_multi_line_assert_is_one_statement_reported_at_its_first_line(self):
        text = diff('tests/test_wf.py', 'self.assertEqual(', '    step["uses"],', '    "actions/checkout@v4")')
        self.assertEqual([n for _, n, _ in mod.findings(text, (), lambda p: True)], [1])

    def test_an_unbalanced_line_does_not_swallow_the_asserts_after_it(self):
        text = diff('tests/test_wf.py', 'x = foo("(",', 'assert upload["uses"] == "a@v4"',
                    "assert steps[0]['with']['persist-credentials'] == 'false'")
        self.assertEqual([n for _, n, _ in mod.findings(text, (), lambda p: True)], [2, 3])

    def test_a_comparison_with_a_computed_value_is_not_flagged(self):
        for line in ("assert download['uses'] == _shipped_action_pin('actions/download-artifact')",
                     "assert step['uses'] == expected_uses"):
            self.assertEqual(self.flagged(diff('tests/test_wf.py', line)), [], line)

    def test_no_checkout_invariants_are_not_flagged(self):
        for line in ('self.assertFalse(any(step.get("uses", "").startswith("actions/checkout@") for step in steps))',
                     'assert not any(s["uses"].startswith("actions/checkout@") for s in job)'):
            self.assertEqual(self.flagged(diff('tests/test_wf.py', line)), [], line)

    def test_a_file_that_never_reads_workflows_is_not_screened(self):
        line = "assert steps[0]['uses'] == 'actions/checkout@v4'"
        self.assertEqual(self.flagged(diff('tests/test_gate_base_freshness.py', line), coupled=False), [])

    def test_absence_and_cross_step_checks_are_not_flagged(self):
        for line in ('expect(step.with ?? {}, "trusted ref").not.toHaveProperty("ref");',
                     'expect(upload?.with?.path).toBe(scanStep?.env?.GITLEAKS_REPORT_PATH);',
                     "assert set(step['with']) <= set(declared)",
                     "assert 'ref' not in checkout['with']"):
            self.assertEqual(self.flagged(diff('tests/test_wf.py', line)), [], line)

    def test_an_assert_on_uses_is_flagged(self):
        line = 'assert upload["uses"] == "actions/upload-artifact@v4"'
        self.assertEqual(len(self.flagged(diff('tests/test_ratchet.py', line))), 1)

    def test_assert_equal_on_uses_is_flagged(self):
        line = 'self.assertEqual(action["uses"], f"Nitjsefnie-Actions/comment-ratchet@{PIN}")'
        self.assertEqual(len(self.flagged(diff('tests/test_wf.py', line))), 1)

    def test_an_assert_on_with_inputs_is_flagged(self):
        for line in ('assert upload["with"]["name"] == "comment-share-data"',
                     "assert checkout['with']['fetch-depth'] == '2'",
                     'expect(step.with.target).toBe("15")'):
            self.assertEqual(len(self.flagged(diff('tests/test_wf.py', line))), 1, line)

    def test_locating_a_step_by_its_uses_prefix_is_not_flagged(self):
        line = 'upload = next(s for s in steps if s.get("uses", "").startswith("actions/upload-artifact@"))'
        self.assertEqual(self.flagged(diff('tests/test_wf.py', line)), [])

    def test_locating_a_step_by_its_with_inputs_is_not_flagged(self):
        line = 'if _child_value(step["with"], "name") == "ratchet-push")'
        self.assertEqual(self.flagged(diff('tests/test_wf.py', line)), [])

    def test_a_with_consistency_check_is_not_flagged(self):
        line = "assert download['with']['name'] == upload['with']['name']"
        self.assertEqual(self.flagged(diff('tests/test_wf.py', line)), [])

    def test_single_file_persist_credentials_is_flagged(self):
        line = "self.assertIs(steps[0]['with']['persist-credentials'], False)"
        self.assertEqual(len(self.flagged(diff('tests/test_wf.py', line))), 1)

    def test_a_fixture_sha_after_an_at_sign_is_not_flagged(self):
        line = "'      - uses: actions/checkout@1111111111111111111111111111111111111111 # v1\\n'"
        self.assertEqual(self.flagged(diff('tests/test_gate.py', line), {SHA}), [])

    def test_behaviour_asserts_are_not_flagged(self):
        for line in ('assert stage["if"] == "github.event_name == \'push\'"',
                     'assert wf["permissions"] == {"contents": "read"}',
                     'assert stage["env"]["HEAD_SHA"] == "${{ github.sha }}"'):
            self.assertEqual(self.flagged(diff('tests/test_wf.py', line)), [], line)

    def test_fixture_shas_without_an_action_ref_are_not_flagged(self):
        line = "BASE = '1234567890abcdef1234567890abcdef12345678'"
        self.assertEqual(self.flagged(diff('tests/test_action.py', line)), [])

    def test_workflow_files_and_docs_are_out_of_scope(self):
        line = f'      - uses: Nitjsefnie-Actions/comment-ratchet@{SHA} # v1.0.0'
        self.assertEqual(self.flagged(diff('.github/workflows/comment-share.yml', line)), [])
        self.assertEqual(self.flagged(diff('scripts/ci/release.py', f'PIN = "{SHA}"'), {SHA}), [])

    def test_removed_lines_are_not_flagged(self):
        self.assertEqual(self.flagged(diff('tests/test_wf.py', removed=[f'PIN = "{SHA}"']), {SHA}), [])

    def test_js_and_shell_tests_are_in_scope(self):
        self.assertEqual(len(self.flagged(diff('tests/workflow.test.ts', 'expect(job.uses).toEqual("actions/checkout@v4")'))), 1)
        self.assertEqual(len(self.flagged(diff('tests/run.sh', f'grep -q "claim@{SHA}" "$wf"'), {SHA})), 1)
        self.assertEqual(len(self.flagged(diff('tests/wf.test.ts', 'expect(step).toHaveProperty("uses", x)'))), 1)

    def test_line_numbers_follow_the_hunk(self):
        text = diff('tests/test_wf.py', 'x = 1', f'PIN = "{SHA}"')
        self.assertEqual([n for _, n, _ in mod.findings(text, {SHA}, lambda p: True)], [2])


# ---------------------------------------------------------------------------
# The widened screen: Dockerfile base images and tool-version pins.
# ---------------------------------------------------------------------------
import base64
import contextlib
import io
import json
import subprocess

# Nitjsefnie/Overflow commit 64fe54d09db178d8ab5be8fb03b60d63463b0106, whole
# (`git show 64fe54d0 --format=`): a Dependabot-shaped base bump that also
# re-pins the test's constants. The test's two added constants must be flagged.
OVERFLOW_64FE54D0 = r'''diff --git a/Dockerfile b/Dockerfile
index bc93f22c..f57f4b4d 100644
--- a/Dockerfile
+++ b/Dockerfile
@@ -8,7 +8,7 @@
 # The app container applies pending migrations (scripts/migrate.ts) before the
 # server starts, and serves on port 3000.
 
-FROM node:24.17.0-bookworm-slim@sha256:862263c612aa437e3037674b85419622a9d93bff80aa1eee5398dfe686375532 AS deps
+FROM node:24.21.0-bookworm-slim@sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6 AS deps
 WORKDIR /app
 RUN corepack enable
 COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
@@ -37,7 +37,7 @@ FROM deps AS prod-deps
 RUN rm -rf node_modules \
   && pnpm install --frozen-lockfile --prod
 
-FROM node:24.17.0-bookworm-slim@sha256:862263c612aa437e3037674b85419622a9d93bff80aa1eee5398dfe686375532 AS runtime
+FROM node:24.21.0-bookworm-slim@sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6 AS runtime
 # Immutable provenance (issue 461). An image built from this Dockerfile must
 # name the exact source it was built from: a preserved image used to report
 # Config.Labels=null, with nothing tying the running bytes to a reviewed
@@ -53,8 +53,8 @@ RUN if [ -z "$SOURCE_SHA" ]; then \
       exit 1; \
     fi
 LABEL org.opencontainers.image.revision=$SOURCE_SHA \
-      org.opencontainers.image.base.name="docker.io/library/node:24.17.0-bookworm-slim" \
-      org.opencontainers.image.base.digest="sha256:862263c612aa437e3037674b85419622a9d93bff80aa1eee5398dfe686375532"
+      org.opencontainers.image.base.name="docker.io/library/node:24.21.0-bookworm-slim" \
+      org.opencontainers.image.base.digest="sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6"
 WORKDIR /app
 ENV NODE_ENV=production
 ENV NEXT_TELEMETRY_DISABLED=1
diff --git a/deploy/container.md b/deploy/container.md
index 0c4fca3f..72507509 100644
--- a/deploy/container.md
+++ b/deploy/container.md
@@ -18,7 +18,7 @@ packages behind in pnpm's virtual store), and `runtime` carries the built
 output, the
 migrations, the production-only dependency tree, and the code the start
 command needs onto a clean base. The `deps` and `runtime` stages pin
-`node:24.17.0-bookworm-slim` by digest, and the runtime stage labels every
+`node:24.21.0-bookworm-slim` by digest, and the runtime stage labels every
 image with the full source revision it was built from — an image without
 that label cannot be built (issue 461) — declares a `HEALTHCHECK` against
 `/api/readiness`, the endpoint that answers 200 only when the database is
@@ -66,7 +66,7 @@ build it with the script first. To build under another tag, pass it to
 override file.
 
 The image builds from digest-pinned bases —
-`node:24.17.0-bookworm-slim@sha256:862263c612aa437e3037674b85419622a9d93bff80aa1eee5398dfe686375532` for the application image and
+`node:24.21.0-bookworm-slim@sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6` for the application image and
 `postgres:17-alpine@sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73` for the database — so the same source always resolves the
 same base bytes. A bare `docker build` without `--build-arg SOURCE_SHA=...`
 fails loudly in the Dockerfile's guard instead of producing an unlabelled
@@ -114,9 +114,10 @@ context because the script exports only committed files with `git archive`;
 the tracked placeholder `.env.example` does enter. Compose supplies `.env`
 variables at runtime through `env_file`; it does not build an image.
 
-**The image builds in-image on `node:24.17.0-bookworm-slim`.** The same base
+**The image builds in-image on `node:24.21.0-bookworm-slim`.** The same base
 that compiles the bundle serves it — both `FROM` lines pinned by digest — and
-`engines` in `package.json` pins that Node version for both. Copying the
+the image's Node base moves on its own patch updates, while `engines` in
+`package.json` pins the Node version CI and the host deployment use. Copying the
 built release out of the image (the host path's release directories) was
 rejected because it reintroduces the artifact-out-of-band problem the
 container exists to close: the image, not the tree it was built from, is the
diff --git a/tests/deploy/container-image.test.ts b/tests/deploy/container-image.test.ts
index 1a25d179..cb787d37 100644
--- a/tests/deploy/container-image.test.ts
+++ b/tests/deploy/container-image.test.ts
@@ -20,9 +20,9 @@ const dockerignore = readFileSync(
  * both the FROM lines and the `base.digest` label below — they are a shared
  * literal, and a digest that drifts between them names a different image.
  */
-const NODE_BASE_TAG = "node:24.17.0-bookworm-slim";
+const NODE_BASE_TAG = "node:24.21.0-bookworm-slim";
 const NODE_BASE_DIGEST =
-  "sha256:862263c612aa437e3037674b85419622a9d93bff80aa1eee5398dfe686375532";
+  "sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6";
 
 describe("Dockerfile", () => {
   it("pins the deps and runtime stages to the bookworm-slim base image by digest", () => {
'''

# tests/deploy/container-image.test.ts at that commit, lines 1-30: it reads the Dockerfile.
OVERFLOW_TEST_AT_64FE54D0 = r'''import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { createDockerImageSuite } from "../support/docker-images";

const dockerfile = readFileSync(
  new URL("../../Dockerfile", import.meta.url),
  "utf8",
);
const dockerignore = readFileSync(
  new URL("../../.dockerignore", import.meta.url),
  "utf8",
);

/**
 * The base image both FROM lines build on, pinned by digest (issue 461): the
 * tag stays for readability, the digest is what Docker actually pulls, so the
 * same source always resolves the same base bytes. One constant here pins
 * both the FROM lines and the `base.digest` label below — they are a shared
 * literal, and a digest that drifts between them names a different image.
 */
const NODE_BASE_TAG = "node:24.21.0-bookworm-slim";
const NODE_BASE_DIGEST =
  "sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6";

describe("Dockerfile", () => {
  it("pins the deps and runtime stages to the bookworm-slim base image by digest", () => {
    const pinnedFrom = `${NODE_BASE_TAG}@${NODE_BASE_DIGEST}`;
    const occurrences = dockerfile.split(pinnedFrom).length - 1;
'''

# The same file today (Overflow f769600a, lines 19-36): the tag and digest are
# derived from the Dockerfile with a shape regex, a consistency check the rule keeps.
OVERFLOW_TEST_NOW = r'''// Parse the runtime FROM independently of the production label derivation.
function expectedBaseLabels(): { baseName: string; baseDigest: string } {
  const runtimeFrom = /^FROM\s+(node:\d+\.\d+\.\d+-bookworm-slim)@(sha256:[a-fA-F0-9]{64})\s+AS\s+runtime\s*$/m.exec(dockerfile);
  expect(runtimeFrom, "the runtime stage's digest-pinned node FROM line").not.toBeNull();
  return { baseName: `docker.io/library/${runtimeFrom![1]}`, baseDigest: runtimeFrom![2] };
}

describe("Dockerfile", () => {
  it("pins the deps and runtime stages to the bookworm-slim base image by digest", () => {
    const references = [...dockerfile.matchAll(/^FROM\s+(node:\d+\.\d+\.\d+-bookworm-slim\S*)/gm)]
      .map((match) => match[1]);
    expect(references.length, "the deps and runtime node FROM lines").toBeGreaterThanOrEqual(2);
    for (const reference of references) {
      expect(reference, "each node FROM reference must be digest-pinned")
        .toMatch(/^node:\d+\.\d+\.\d+-bookworm-slim@sha256:[a-fA-F0-9]{64}$/);
    }
    expect(new Set(references).size, "all node stages must use the same tag and digest").toBe(1);
  });
'''

DIGEST = '0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6'
TEST_TS = 'tests/deploy/container-image.test.ts'


def added(path, text):
    """A diff adding every line of text to path."""
    return diff(path, *text.splitlines())


def screen(diff_text, literals, couples=True):
    """Flagged (path, line) with every test file coupled (or not) to every source kind."""
    found = mod.findings(diff_text, (), None, literals, lambda path, kind: couples)
    return [(p, n) for p, n, _ in found]


class DockerfilePins(unittest.TestCase):
    """dockerfile_pins over the FROM grammar: every form flagged, not applicable or out of reach."""

    def test_the_overflow_from_lines(self):
        text = ('ARG SOURCE_SHA\n'
                f'FROM node:24.21.0-bookworm-slim@sha256:{DIGEST} AS deps\n'
                'FROM deps AS build\nFROM deps AS prod-deps\n'
                f'FROM node:24.21.0-bookworm-slim@sha256:{DIGEST} AS runtime\n')
        self.assertEqual(mod.dockerfile_pins(text), {'node:24.21.0-bookworm-slim', '24.21.0-bookworm-slim', DIGEST})

    def test_every_from_form(self):
        h = 'a' * 64
        table = [
            # (Dockerfile, pinned literals): flagged when non-empty
            ('FROM --platform=$BUILDPLATFORM golang:1.23.4 AS build', {'golang:1.23.4', '1.23.4'}),
            ('FROM node:24.1.0 AS x', {'node:24.1.0', '24.1.0'}),
            (f'FROM node@sha256:{h}', {h}),
            ('from node:24.1.0', {'node:24.1.0', '24.1.0'}),
            ('FROM ghcr.io/owner/img:1.2.3', {'img:1.2.3', '1.2.3'}),
            ('FROM localhost:5000/img:1.2.3', {'img:1.2.3', '1.2.3'}),
            ('FROM node:24', {'node:24'}),
            ('FROM \\\n    node:24.1.0', {'node:24.1.0', '24.1.0'}),
            ('ARG BASE=python:3.13.1-slim\nFROM ${BASE}', {'python:3.13.1-slim', '3.13.1-slim'}),
            ('ARG V=3.13.1\nFROM python:$V-slim AS app', {'python:3.13.1-slim', '3.13.1-slim'}),
            ('FROM python:${V:-3.12.8}', {'python:3.12.8', '3.12.8'}),
            ('ARG V="3.13.1"\nFROM python:${V}', {'python:3.13.1', '3.13.1'}),
            # not applicable: a stage alias, scratch, no tag, a tag naming no version
            ('FROM node:24.1.0 AS deps\nFROM deps AS build', {'node:24.1.0', '24.1.0'}),
            ('FROM scratch', set()),
            ('FROM node', set()),
            ('FROM node:lts-slim', set()),
            # out of reach: an ARG with no default leaves nothing to read
            ('ARG V\nFROM python:${V}', set()),
        ]
        for text, want in table:
            with self.subTest(text=text):
                self.assertEqual(mod.dockerfile_pins(text), want)


class ManifestPins(unittest.TestCase):
    def test_requirements_exact_pins_only(self):
        text = ('requests==2.32.3\nDjango[argon2]==5.1.4 ; python_version >= "3.10"\nfoo>=1.0\n'
                'bar===1.0.0  # arbitrary equality\n# baz==9.9.9\n-r other.txt\nsix==2\n')
        self.assertEqual(mod.manifest_pins('requirements', text), {'2.32.3', '5.1.4', '1.0.0'})

    def test_package_json_exact_dependency_engine_and_manager_pins(self):
        text = json.dumps({'name': 'x', 'version': '0.1.0',
                           'dependencies': {'a': '1.2.3', 'b': '^1.2.3', 'c': '=4.5.6', 'd': 'v7.8.9'},
                           'devDependencies': {'e': '10.0.0-rc.1'}, 'peerDependencies': {'f': '~2.0.0'},
                           'optionalDependencies': {'g': '3.3.3'}, 'engines': {'node': '24.21.0', 'npm': '>=10'},
                           'packageManager': 'pnpm@9.15.4+sha512.abc'})
        self.assertEqual(mod.manifest_pins('package.json', text),
                         {'1.2.3', '4.5.6', '7.8.9', '10.0.0-rc.1', '3.3.3', '24.21.0', '9.15.4'})

    def test_an_unparsable_package_json_cannot_be_screened(self):
        with self.assertRaises(mod.CannotScreen):
            mod.manifest_pins('package.json', '{"dependencies": ')

    def test_tool_versions_python_version_and_nvmrc(self):
        self.assertEqual(mod.manifest_pins('.tool-versions', 'nodejs 24.21.0\npython 3.13.1 3.12.8\n# c 1.1\nruby system\n'),
                         {'24.21.0', '3.13.1', '3.12.8'})
        self.assertEqual(mod.manifest_pins('.tool-versions', 'golang ref:1.22.0\nrust path:/opt/1.2\n'), set())
        self.assertEqual(mod.manifest_pins('.python-version', '3.13.1\n# 3.9.0\n'), {'3.13.1'})
        self.assertEqual(mod.manifest_pins('.nvmrc', 'v24.21.0\n'), {'24.21.0'})
        self.assertEqual(mod.manifest_pins('.nvmrc', 'lts/iron\n'), set())
        self.assertEqual(mod.manifest_pins('.nvmrc', '24\n'), set())  # no dot: a bare number is not screened

    def test_inline_installs_in_workflow_run_blocks(self):
        text = ('      - run: pip install ruff==0.8.4 "mypy==1.13.0"\n'
                "      - run: python -m pip install -U 'pytest==8.3.4' -r requirements.txt\n"
                '      - run: |\n          pip install \\\n            black==24.10.0\n'
                '      - run: npm i -g pnpm@9.15.4\n'
                '      - run: npm install --save-dev @scope/tool@1.2.3 left-pad@^1.3.0\n'
                '      - run: echo foo==9.9.9 bar@8.8.8\n')
        self.assertEqual(mod.manifest_pins('workflow', text),
                         {'0.8.4', '1.13.0', '8.3.4', '24.10.0', '9.15.4', '1.2.3'})

    def test_each_source_path_has_its_kind(self):
        table = {'Dockerfile': 'dockerfile', 'deploy/api.Dockerfile': 'dockerfile', 'Containerfile': 'dockerfile',
                 'requirements.txt': 'requirements', 'requirements-dev.txt': 'requirements',
                 'ci/requirements.lock.txt': 'requirements', 'package.json': 'package.json',
                 'web/package.json': 'package.json', '.tool-versions': '.tool-versions',
                 '.python-version': '.python-version', '.nvmrc': '.nvmrc',
                 '.github/workflows/ci.yml': 'workflow', '.github/workflows/ci.yaml': 'workflow',
                 'Dockerfile.dev': None, 'docs/requirements.md': None, 'package-lock.json': None,
                 '.github/workflows/README.md': None, 'pyproject.toml': None}
        for path, kind in table.items():
            with self.subTest(path=path):
                self.assertEqual(mod.source_kind(path), kind)


class WidenedFindings(unittest.TestCase):
    def test_the_overflow_bump_flags_the_test_constants(self):
        # The pins come from the same diff's Dockerfile hunk; the test file's
        # coupling from its own text, which reads the Dockerfile.
        found = mod.findings(OVERFLOW_64FE54D0, (), None, None,
                             lambda path, kind: mod.couples(OVERFLOW_TEST_AT_64FE54D0, kind))
        self.assertEqual([(p, n) for p, n, _ in found], [(TEST_TS, 23), (TEST_TS, 25)])

    def test_the_derived_tag_and_digest_are_not_flagged(self):
        literals = {'dockerfile': mod.dockerfile_pins(
            f'FROM node:24.22.0-bookworm-slim@sha256:{"a5" * 32} AS deps\n')}
        self.assertEqual(screen(added(TEST_TS, OVERFLOW_TEST_NOW), literals), [])

    def test_asserting_the_dockerfile_contains_the_tag_is_still_a_pin(self):
        # Mentioning the Dockerfile is not the exemption; deriving the value is.
        literals = {'dockerfile': {'node:24.21.0-bookworm-slim', '24.21.0-bookworm-slim', DIGEST}}
        text = diff(TEST_TS, 'expect(dockerfile).toContain("node:24.21.0-bookworm-slim");',
                    'expect(fromLines(dockerfile)[0]).toBe(runtimeFrom(dockerfile));')
        self.assertEqual(screen(text, literals), [(TEST_TS, 1)])

    def test_each_family_flags_a_held_literal_only_in_a_file_coupled_to_its_source(self):
        cases = [
            ('dockerfile', 'node:24.21.0-bookworm-slim', 'const TAG = "node:24.21.0-bookworm-slim";'),
            ('dockerfile', '24.21.0-bookworm-slim', 'const TAG = "24.21.0-bookworm-slim";'),
            ('dockerfile', DIGEST, f'const DIGEST = "sha256:{DIGEST}";'),
            ('requirements', '2.32.3', 'assert requests.__version__ == "2.32.3"'),
            ('package.json', '24.21.0', 'expect(pkg.engines.node).toBe("24.21.0");'),
            ('.tool-versions', '3.13.1', 'PYTHON = "3.13.1"'),
            ('.python-version', '3.13.1', 'assert sys_version == "3.13.1"'),
            ('.nvmrc', '24.21.0', 'expect(nvmrc.trim()).toBe("v24.21.0");'),
            ('workflow', '0.8.4', 'assert "ruff==0.8.4" in wf_text'),
        ]
        for kind, literal, line in cases:
            with self.subTest(kind=kind, line=line):
                text = diff('tests/test_pins.py', line)

                def coupled(_path, k, kind=kind):
                    return k == kind

                def other(_path, k, kind=kind):
                    return k != kind
                self.assertEqual([(p, n) for p, n, _ in mod.findings(text, (), None, {kind: {literal}}, coupled)],
                                 [('tests/test_pins.py', 1)])
                self.assertEqual(mod.findings(text, (), None, {kind: {literal}}, other), [])

    def test_a_parser_tested_on_fixture_text_is_not_flagged(self):
        text = diff('tests/test_dockerfile_parser.py', 'assert parse("FROM node:24.21.0-bookworm-slim") == "node"')
        literals = {'dockerfile': {'node:24.21.0-bookworm-slim'}}
        self.assertEqual(screen(text, literals, couples=False), [])

    def test_a_version_is_matched_whole(self):
        literals = {'requirements': {'1.2.3'}}
        for line, want in (('assert v == "1.2.3"', 1), ('assert v == "11.2.3"', 0), ('assert v == "1.2.34"', 0),
                           ('assert v == "1.2.3.4"', 0), ('assert v == "pkg==1.2.3"', 1), ('assert v == "a1.2.3"', 0)):
            with self.subTest(line=line):
                self.assertEqual(len(screen(diff('tests/test_v.py', line), literals)), want)

    def test_prose_holds_no_pin_and_a_quoted_value_does(self):
        # Seen in prior screenings: comments and
        # docstrings that name a version cannot fail on a bump; code values can.
        literals = {'requirements': {'2.8.0', '1.30.1'}}
        for line, want in (('// Measured on zizmor 1.30.1 (probe leg n4b)', 0),
                           (" * 1007's exact bad manifest: `zizmor==1.30.1` carrying hashes", 0),
                           ('# ``~=2.8.0`` means >=2.8.0,==2.* and ``==`` pins exactly', 0),
                           ('the binding floor is therefore 2.8.0.', 0),
                           ('"""eslint-plugin-react 7.37.5\'s peer range stops at 2.8.0.', 0),
                           ('"urllib3": Version("2.8.0"),', 1),
                           ('content: `zizmor==1.30.1 --hash=sha256:${"a".repeat(64)}\\n`,', 1),
                           ("x = 'urllib3==2.8.0'  # it's pinned", 1)):
            with self.subTest(line=line):
                self.assertEqual(len(screen(diff('tests/test_v.py', line), literals)), want)

    def test_a_pin_outside_any_quoted_string_is_out_of_reach(self):
        # Disclosed in the docstring: a bare shell word or a number is not read.
        literals = {'requirements': {'0.8.4'}}
        self.assertEqual(screen(diff('tests/run.sh', 'grep -q ruff==0.8.4 requirements.txt'), literals), [])

    def test_version_shape_stays_and_digest_shape_is_flagged(self):
        literals = {'requirements': {'2.32.3'}, 'dockerfile': {'node:24.21.0-bookworm-slim', DIGEST}}
        self.assertEqual(screen(diff('tests/test_shape.py', r'assert re.fullmatch(r"\d+\.\d+\.\d+", version)'), literals), [])
        line = r'expect(ref).toMatch(/^node:\d+\.\d+\.\d+-bookworm-slim@sha256:[a-f0-9]{64}$/);'
        self.assertEqual(screen(diff('tests/test_shape.py', line), literals), [('tests/test_shape.py', 1)])
        # An absence assertion (the digest must NOT appear) pins nothing.
        for absent in (r'expect(readFileSync(fileURLToPath(url), "utf8")).not.toMatch(/sha256:[0-9a-f]{64}/);',
                       r'assert not re.search(r"sha256:[0-9a-f]{64}", text)'):
            with self.subTest(absent=absent):
                self.assertEqual(screen(diff('tests/test_shape.py', absent), literals), [])

    def test_couples_is_one_predicate_over_every_source_kind(self):
        reads = 'const text = readFileSync(path.join(root, "{}"), "utf8");'
        for kind, name in (('workflow', '.github/workflows/ci.yml'), ('dockerfile', 'Dockerfile'),
                           ('requirements', 'requirements-dev.txt'), ('package.json', 'package.json'),
                           ('.tool-versions', '.tool-versions'), ('.python-version', '.python-version'),
                           ('.nvmrc', '.nvmrc')):
            with self.subTest(kind=kind):
                self.assertTrue(mod.couples(reads.format(name), kind))
                self.assertFalse(mod.couples(f'const name = "{name}";', kind))  # mentions, never reads
                self.assertFalse(mod.couples(reads.format('README.md'), kind))  # reads something else

    def test_pins_in_the_diff_count_where_the_test_reads_that_source(self):
        text = (diff('requirements.txt', 'requests==2.32.3')
                + diff('tests/test_deps.py', 'REQS = (ROOT / "requirements.txt").read_text()',
                       'assert "requests==2.32.3" in REQS'))
        self.assertEqual([(p, n) for p, n, _ in mod.findings(text)], [('tests/test_deps.py', 2)])


SHA_HEAD = 'c' * 40


def http_fail(stderr='HTTP 404: Not Found'):
    return subprocess.CalledProcessError(1, ['gh'], output='', stderr=stderr)


class Main(unittest.TestCase):
    """main() end to end with the gh calls answered by a double that refuses anything it does not model."""

    def run_main(self, diff_text, blobs, contents, truncated=False):
        calls = []
        tree = [{'path': path, 'type': 'blob', 'sha': f'blob-{n}'} for n, path in enumerate(blobs)]
        by_sha = {f'blob-{n}': blobs[path] for n, path in enumerate(blobs)}

        def fake_gh(*args):
            calls.append(args)
            if args == ('pr', 'view', '9', '-R', 'o/r', '--json', 'headRefOid'):
                return json.dumps({'headRefOid': SHA_HEAD})
            if args == ('pr', 'diff', '9', '-R', 'o/r'):
                return diff_text
            if args == ('api', f'repos/o/r/git/trees/{SHA_HEAD}?recursive=1'):
                return json.dumps({'sha': SHA_HEAD, 'tree': tree, 'truncated': truncated})
            if len(args) == 2 and args[0] == 'api' and args[1].startswith('repos/o/r/git/blobs/'):
                blob = by_sha[args[1].rsplit('/', 1)[1]]
                if isinstance(blob, Exception):
                    raise blob
                return json.dumps({'content': base64.b64encode(blob.encode()).decode(), 'encoding': 'base64'})
            if (len(args) == 4 and args[0] == 'api' and args[2:] == ('-H', 'Accept: application/vnd.github.raw')
                    and args[1].startswith('repos/o/r/contents/') and args[1].endswith(f'?ref={SHA_HEAD}')):
                text = contents[args[1][len('repos/o/r/contents/'):-len(f'?ref={SHA_HEAD}')]]
                if isinstance(text, Exception):
                    raise text
                return text
            raise AssertionError(f'unmodeled gh call {args}')

        out, err = io.StringIO(), io.StringIO()
        saved = mod._gh
        setattr(mod, '_gh', fake_gh)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = mod.main(['--repo', 'o/r', '--pr', '9'])
        finally:
            setattr(mod, '_gh', saved)
        return code, out.getvalue().splitlines(), err.getvalue().splitlines(), calls

    DOCKERFILE = f'FROM node:24.21.0-bookworm-slim@sha256:{DIGEST} AS deps\nFROM deps AS build\n'
    SUMMARY_FOUND = ('{} candidate test line(s) holding or asserting a pinned workflow action or input, '
                     "Dockerfile base image or tool version: judge each against the repository's own rule — no "
                     'test asserts on a pinned version, tag, SHA or digest of any dependency: a workflow `uses:` '
                     "value (the action's SHA, release, version comment or pin shape) or its `with:` inputs, a "
                     'Dockerfile `FROM` base image, a tool version or a package version, and a legitimate bump '
                     'must never fail a test — and delete those that pin a dependency; this screen misses every '
                     'pin it cannot read as a literal from a source it reads (see --help), so it clears nothing '
                     'it does not list.')
    SUMMARY_NONE = ('no candidate test lines among the workflow, Dockerfile base-image and tool-version pins '
                    'this screen reads; it misses every pin it cannot read as a literal from a source it reads '
                    '(see --help), so an empty list is not a proof.')

    def test_the_overflow_bump_through_main(self):
        test_diff = OVERFLOW_64FE54D0[OVERFLOW_64FE54D0.index('diff --git a/tests/'):]  # the test hunk alone
        code, out, err, _ = self.run_main(test_diff, {'Dockerfile': self.DOCKERFILE},
                                          {TEST_TS: OVERFLOW_TEST_AT_64FE54D0})
        self.assertEqual(code, 1)
        self.assertEqual(out, [f'{TEST_TS}:23: const NODE_BASE_TAG = "node:24.21.0-bookworm-slim";',
                               f'{TEST_TS}:25: "sha256:{DIGEST}";'])
        self.assertEqual(err, [self.SUMMARY_FOUND.format(2)])

    def test_no_candidates_says_an_empty_list_is_no_proof(self):
        code, out, err, calls = self.run_main(added(TEST_TS, OVERFLOW_TEST_NOW), {'Dockerfile': self.DOCKERFILE},
                                              {TEST_TS: OVERFLOW_TEST_AT_64FE54D0})
        self.assertEqual((code, out, err), (0, [], [self.SUMMARY_NONE]))
        self.assertIn(('api', f'repos/o/r/contents/{TEST_TS}?ref={SHA_HEAD}', '-H',
                       'Accept: application/vnd.github.raw'), calls)

    def test_an_unreadable_manifest_is_named_and_is_no_clean_result(self):
        reqs = 'assert pkg["dependencies"]["a"] == "1.2.3"'
        code, out, err, _ = self.run_main(
            diff('tests/test_pkg.py', reqs), {'package.json': '{"dependencies": ', 'Dockerfile': http_fail()},
            {'tests/test_pkg.py': 'PKG = json.loads((ROOT / "package.json").read_text())\n'})
        self.assertEqual(code, 2)
        self.assertEqual(out, [])
        self.assertEqual(err, [
            'could not screen package.json: not valid JSON (Expecting value: line 1 column 18 (char 17))',
            'could not screen Dockerfile: HTTP 404: Not Found',
            '2 file(s) could not be screened (above); no candidates among the rest proves nothing about them, '
            'and an empty list is not a proof.'])

    def test_a_screen_that_crashes_exits_2_not_1(self):
        # An uncaught exception exits 1, which a wrapper would read as "candidates": a crash is "could not screen".
        deep = 'assert step["with"]["name"] == ' + '+'.join(['"a"'] * 5000)
        code, out, err, _ = self.run_main(
            diff('tests/test_wf.py', deep), {},
            {'tests/test_wf.py': 'WF = (ROOT / ".github/workflows/ci.yml").read_text()\n'})
        self.assertEqual((code, out), (2, []))
        self.assertTrue(err[-1].startswith('could not screen o/r PR 9: RecursionError'), err)

    def test_an_unreadable_test_file_is_named(self):
        code, out, err, _ = self.run_main(diff('tests/test_pkg.py', 'X = "1.2.3"'),
                                          {'package.json': '{"dependencies": {"a": "1.2.3"}}'},
                                          {'tests/test_pkg.py': http_fail('HTTP 502: Bad Gateway')})
        self.assertEqual((code, out), (2, []))
        self.assertEqual(err[0], 'could not screen tests/test_pkg.py: HTTP 502: Bad Gateway')

    def test_a_truncated_tree_is_named(self):
        code, out, err, _ = self.run_main(diff('tests/test_x.py', 'x = 1'), {}, {'tests/test_x.py': 'x = 1\n'},
                                          truncated=True)
        self.assertEqual((code, out), (2, []))
        self.assertEqual(err[0], f'could not screen the tree at {SHA_HEAD}: GitHub truncated the recursive listing')

    def test_candidates_and_unscreened_files_exit_2_and_name_both(self):
        code, out, err, _ = self.run_main(
            diff('tests/test_pkg.py', 'assert v == "1.2.3"'),
            {'package.json': '{"dependencies": {"a": "1.2.3"}}', 'Dockerfile': http_fail()},
            {'tests/test_pkg.py': 'PKG = json.loads((ROOT / "package.json").read_text())\n'})
        self.assertEqual(code, 2)
        self.assertEqual(out, ['tests/test_pkg.py:1: assert v == "1.2.3"'])
        self.assertEqual(err, ['could not screen Dockerfile: HTTP 404: Not Found', self.SUMMARY_FOUND.format(1)])

    def test_workflow_sha_pins_still_come_from_the_head(self):
        wf = f'      - uses: o/claim@{SHA} # v1.0.0\n      - run: pip install ruff==0.8.4\n'
        code, out, _, _ = self.run_main(
            diff('tests/test_wf.py', f'PIN = "{SHA}"', 'assert "ruff==0.8.4" in WF'),
            {'.github/workflows/c.yml': wf},
            {'tests/test_wf.py': 'WF = (ROOT / ".github/workflows/c.yml").read_text()\n'})
        self.assertEqual(code, 1)
        self.assertEqual(out, [f'tests/test_wf.py:1: PIN = "{SHA}"', 'tests/test_wf.py:2: assert "ruff==0.8.4" in WF'])


class ScreeningRegressions(unittest.TestCase):
    def screen(self, source):
        return mod.findings(diff('tests/test_workflow.py', *source.splitlines()), {SHA}, lambda p: True)

    def test_parser_fixture_assertions(self):
        # Each scan location is an independent entry, even where the copies agree.
        for repo, parser, line in (
            ('pr-gate:457', "module.workflow_steps(text, 'w.yml')['actionlint']", f"assert steps[0]['uses'] == 'actions/checkout@{'1' * 40}'"),
            ('pr-gate:458', "module.workflow_steps(text, 'w.yml')['actionlint']", "assert steps[1]['uses'] == './'"),
            ('gh-widgets:464', "module.workflow_jobs(text, 'w.yml')['actionlint'].steps", f"assert steps[0]['uses'] == 'actions/checkout@{'1' * 40}'"),
            ('gh-widgets:465', "module.workflow_jobs(text, 'w.yml')['actionlint'].steps", "assert steps[1]['uses'] == './'"),
            ('ai-researcher:742', "module.workflow_steps(text, 'w.yml')['actionlint']", f"assert steps[0]['uses'] == 'actions/checkout@{'1' * 40}'"),
            ('ai-researcher:743', "module.workflow_steps(text, 'w.yml')['actionlint']", "assert steps[1]['uses'] == './'"),
        ):
            with self.subTest(location=repo):
                self.assertEqual(self.screen("text = ('jobs:\\n'\n"
                                             "        '  actionlint:\\n')\n"
                                             f"steps = {parser}\n{line}"), [])

    def test_classifier_fixture(self):
        self.assertEqual(self.screen("""assert _cache_write_reason(
    {'uses': 'example/setup-thing@v1', 'with': {'cache': 'false'}},
    'sample', 1) is not None"""), [])

    def test_pin_sync_fixture_output(self):
        for location, value in (('1003', '{reviewed} # v1.0.1'), ('1004', '{divergent} # v1.0.1'),
                                ('1015', '{reviewed} # v1.0.1'), ('1016', '{reviewed} # v1.1.0')):
            with self.subTest(location='pr-gate:' + location):
                self.assertEqual(self.screen(f"assert f'{value}' in result.stdout, result.stdout"), [])

    def test_consistency_checks(self):
        entries = [
            ('pr-gate:206', "reviewed = _shipped_action_pin('actions/upload-artifact')\nassert upload['uses'] == f'actions/upload-artifact@{reviewed}'"),
            ('pr-gate:235', "assert download['uses'] == (\n    'actions/download-artifact@'\n    + _shipped_action_pin('actions/download-artifact'))"),
            ('pr-gate:255', "reviewed = _shipped_action_pin('actions/upload-artifact')\nassert upload['uses'] == f'actions/upload-artifact@{reviewed}'"),
            ('pr-gate:777', "assert documented['jobs']['gate']['steps'][0]['uses'] == (\n    f'{action}@{_shipped_action_pin(action)}')"),
            ('pr-gate:1134', "assert steps[0]['uses'] == (\n    f\"actions/checkout@{_shipped_action_pin('actions/checkout')}\")"),
            ('claudit:548', '''assert {line for line in overlay.splitlines() if line.startswith("cp ")} == {
    f'cp "{download["with"]["path"]}/{filename}" {target}'
    for filename, target in [
        ("pricing.json", "src/pricing.json"),
        ("constants.py", "backend/constants.py"),
        ("pricing_notices.json", "src/pricing_notices.json"),
    ]
}'''),
            ('claudit:585', '''assert outputs == {f'{raw["with"]["path"]}/{filename}' for filename in
    ["pricing.json", "constants.py", "pricing_notices.json", "commit-msg.txt", "base.txt"]}'''),
            ('claudit-scorecard:287', '''with_ = scorecard_step.get("with") or {}
assert (upload.get("with") or {}).get("sarif_file") == with_[
    "results_file"], (
    "the SARIF upload reads a different file than the analysis wrote")'''),
            ('claudit-scorecard:399', '''assert str(upload["uses"]).partition("@")[2] in shas, (
    "scorecard's SARIF upload and codeql.yml's pins disagree: "
    f"{upload['uses']} against {sorted(shas)}")'''),
            ('Overflow:2194', r'expect(download.with?.["run-id"]).toBe(`\${{ steps.${awaiter.id}.outputs.run_id }}`);'),
            ('gh-widgets:992', '''self.assertEqual([step["uses"] for step in steps if "uses" in step],
    [downloads[0]["uses"]])'''),
        ]
        for location, source in entries:
            with self.subTest(location=location):
                self.assertEqual(self.screen(source), [])

    def test_security_identity_and_absence(self):
        entries = [
            ('gh-widgets:1195', '''self.assertEqual(
    [step["uses"].split("@", maxsplit=1)[0]
     for step in self.steps if "uses" in step],
    ["actions/download-artifact"])'''),
            ('ai-researcher:89', '''assert not [s for s in self.push["steps"]
    if s.get("uses", "").startswith("actions/checkout@")], (
    "the push job runs no repository code: no checkout step")'''),
            ('Overflow-calibrate:194', '''expect(
    actions.map((step) => step.uses!.replace(/@[0-9a-f]{40}$/, "@")),
    "the push job's only action is the checkout",
).toEqual(["actions/checkout@"]);'''),
            ('Overflow-comment-share:126', r'expect(steps.some((step) => /^actions\/checkout(?:@|$)/.test(step.uses ?? ""))).toBe(false);'),
        ]
        for location, source in entries:
            with self.subTest(location=location):
                self.assertEqual(self.screen(source), [])

    def test_uses_in_assertion_messages_only(self):
        for location, source in (
            ('Overflow-dependency:526', '''expect(
    effective(step, key),
    `step "${step.name ?? step.uses}" runs pnpm and must resolve ${key} from the workflow, ` +
        "not from a value its own env block shadows",
).toBe(value);'''),
            ('Overflow-secret-scan:246', '''expect(
    step["continue-on-error"],
    `${step.name ?? step.uses ?? "a step"} must not tolerate failure`,
).toBeUndefined();'''),
        ):
            with self.subTest(location=location):
                self.assertEqual(self.screen(source), [])

    def test_workflow_call_structure(self):
        self.assertEqual(self.screen("assert Path(caller.get('uses', '').split('@', 1)[0]).name != 'actionlint.yml'"), [])

    def test_quoted_brackets_do_not_join_assertions(self):
        source = """assert '${{' not in steps[index]['run'], (
    'the pin-sync step must not interpolate expressions: zizmor flags '
    'script injection through ${{ }} in run blocks')
assert names[index + 1] == 'Install actionlint', (
    'the pin-sync step must precede Install actionlint')
assert step['uses'] == 'actions/checkout@v4'
"""
        found = self.screen(source)
        self.assertEqual([(n, text) for _, n, text in found],
                         [(6, "assert step['uses'] == 'actions/checkout@v4'")])

    def test_single_file_full_history(self):
        self.assertEqual(len(self.screen("assert checkout['with'].get('fetch-depth') == '0', checkout")), 1)

    def test_with_dictionary_misses(self):
        for location, source in (
            ('pr-gate:229', """assert checkout['with'] == {'ref': '${{ github.sha }}',
    'fetch-depth': '0',
    'persist-credentials': 'false'}, (
    'the checkout must be the exact merge the coverage report describes')"""),
            ('pr-gate:1135', "assert steps[0]['with'] == {'fetch-depth': '0', 'persist-credentials': 'false'}"),
        ):
            with self.subTest(location=location):
                self.assertEqual(len(self.screen(source)), 1)

    def test_with_alias_miss(self):
        self.assertEqual([(n, text) for _, n, text in self.screen('''with_ = scorecard_step.get("with") or {}
assert with_.get("results_format") == "sarif"''')],
                         [(2, 'assert with_.get("results_format") == "sarif"')])

    def test_exemptions_do_not_hide_nearby_pins(self):
        for source in (
            "assert checkout['with']['persist-credentials'] is True",
            'expect(step.with.cache).toBe(false);',
            "name = 'tests.yml'\nsteps = _workflow(name)['steps']\nassert steps[0]['uses'] == 'actions/checkout@v4'",
            "PIN = 'v4'\nassert step['uses'] == f'actions/checkout@{PIN}'",
            "with_ = step.get('with') or {}\nassert with_.get('fetch-depth') == '2'",
        ):
            with self.subTest(source=source):
                self.assertEqual(len(self.screen(source)), 1)

    def test_aliases_are_local_and_reassigned(self):
        for reset in ("def test_second():", "async def test_second():", "class Second:", "with_ = other"):
            with self.subTest(reset=reset):
                self.assertEqual(self.screen('with_ = step.get("with") or {}\n' + reset +
                                             '\nassert with_.get("results_format") == "sarif"'), [])

    def test_single_file_history_and_credentials(self):
        for source in ("assert checkout['with']['fetch-depth'] == '0'",
                       "assert checkout.get('with', {}).get('fetch-depth') == 0",
                       "assert checkout['with']['persist-credentials'] is False"):
            with self.subTest(source=source):
                self.assertEqual(len(self.screen(source)), 1)

    def test_comparison_and_binding_boundaries(self):
        entries = [
            ("assert step.uses == 'actions/checkout@v4'", 1),
            ("assert step['uses'] == 'actions/' + 'checkout@v4'", 1),
            ("assert step['with']['mode'] in ('fast', 'slow')", 1),
            ("assert step['with']['mode'] in (expected, 'fast')", 1),
            ("assert upload['with']['path'] == f\"{download['with']['path']}/report\"", 0),
            ("assert step.get(key) == 'fixed'", 0),
            ("assert step[key] == 'fixed'", 0),
            ("self.assertNotIn('ref', checkout['with'])", 0),
            ("assert True, step['uses']", 0),
            ("assert name == 'scan', step['uses']", 0),
            ("text = 'jobs: {}'\nsteps = module.workflow_steps(text, 'w.yml')\n"
             "copied = steps\nassert copied[0]['uses'] == './'", 0),
            ("reviewed = shipped_pin('checkout')\ncopy = reviewed\n"
             "assert step['uses'] == f'checkout@{copy}'", 0),
        ]
        for source, count in entries:
            with self.subTest(source=source):
                self.assertEqual(len(self.screen(source)), count)

    def test_input_alias_does_not_cross_files(self):
        patch = (diff('tests/test_a.py', 'with_ = step.get("with") or {}') +
                 diff('tests/test_b.py', 'assert with_.get("results_format") == "sarif"'))
        self.assertEqual(mod.findings(patch, (), lambda p: True), [])

    def test_braced_input_does_not_swallow_pin_assignment(self):
        source = 'assert step["with"]["name"] == "${{"\n' + f'PIN = "{SHA}"'
        self.assertEqual([n for _, n, _ in self.screen(source)], [1, 2])

    def test_history_values_are_not_boolean_values(self):
        for source in ("assert checkout['with']['fetch-depth'] is False",
                       "assert checkout['with']['persist-credentials'] == 0"):
            with self.subTest(source=source):
                self.assertEqual(len(self.screen(source)), 1)


# BASE 8a592ef verdicts measured once against these embedded patches; runtime reads
# only this data. Removals name the unit's exemptions; additions retain the
# original input misses and statement-boundary corrections from 6881f85.
FIX1_CORPUS = [
    ("test_aliases_are_local_and_reassigned:1", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+with_ = step.get(\"with\") or {}\n+def test_second():\n+assert with_.get(\"results_format\") == \"sarif\"\n", [], [], []),
    ("test_aliases_are_local_and_reassigned:2", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+with_ = step.get(\"with\") or {}\n+async def test_second():\n+assert with_.get(\"results_format\") == \"sarif\"\n", [], [], []),
    ("test_aliases_are_local_and_reassigned:3", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+with_ = step.get(\"with\") or {}\n+class Second:\n+assert with_.get(\"results_format\") == \"sarif\"\n", [], [], []),
    ("test_aliases_are_local_and_reassigned:4", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+with_ = step.get(\"with\") or {}\n+with_ = other\n+assert with_.get(\"results_format\") == \"sarif\"\n", [], [], []),
    ("test_braced_input_does_not_swallow_pin_assignment:5", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert step[\"with\"][\"name\"] == \"${{\"\n+PIN = \"721426819be9e05deb76d56364f671b87fa05fbf\"\n", [1], [], [2]),
    ("test_classifier_fixture:6", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert _cache_write_reason(\n+    {'uses': 'example/setup-thing@v1', 'with': {'cache': 'false'}},\n+    'sample', 1) is not None\n", [1], [1], []),
    ("test_comparison_and_binding_boundaries:7", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert step.uses == 'actions/checkout@v4'\n", [1], [], []),
    ("test_comparison_and_binding_boundaries:8", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert step['uses'] == 'actions/' + 'checkout@v4'\n", [1], [], []),
    ("test_comparison_and_binding_boundaries:9", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert step['with']['mode'] in ('fast', 'slow')\n", [1], [], []),
    ("test_comparison_and_binding_boundaries:10", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert step['with']['mode'] in (expected, 'fast')\n", [1], [], []),
    ("test_comparison_and_binding_boundaries:11", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert upload['with']['path'] == f\"{download['with']['path']}/report\"\n", [], [], []),
    ("test_comparison_and_binding_boundaries:12", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert step.get(key) == 'fixed'\n", [], [], []),
    ("test_comparison_and_binding_boundaries:13", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert step[key] == 'fixed'\n", [], [], []),
    ("test_comparison_and_binding_boundaries:14", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+self.assertNotIn('ref', checkout['with'])\n", [], [], []),
    ("test_comparison_and_binding_boundaries:15", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert True, step['uses']\n", [1], [1], []),
    ("test_comparison_and_binding_boundaries:16", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert name == 'scan', step['uses']\n", [1], [1], []),
    ("test_comparison_and_binding_boundaries:17", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+text = 'jobs: {}'\n+steps = module.workflow_steps(text, 'w.yml')\n+copied = steps\n+assert copied[0]['uses'] == './'\n", [4], [4], []),
    ("test_comparison_and_binding_boundaries:18", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+reviewed = shipped_pin('checkout')\n+copy = reviewed\n+assert step['uses'] == f'checkout@{copy}'\n", [3], [3], []),
    ("test_consistency_checks:19", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+reviewed = _shipped_action_pin('actions/upload-artifact')\n+assert upload['uses'] == f'actions/upload-artifact@{reviewed}'\n", [2], [2], []),
    ("test_consistency_checks:20", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert download['uses'] == (\n+    'actions/download-artifact@'\n+    + _shipped_action_pin('actions/download-artifact'))\n", [1], [1], []),
    ("test_consistency_checks:21", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+reviewed = _shipped_action_pin('actions/upload-artifact')\n+assert upload['uses'] == f'actions/upload-artifact@{reviewed}'\n", [2], [2], []),
    ("test_consistency_checks:22", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert documented['jobs']['gate']['steps'][0]['uses'] == (\n+    f'{action}@{_shipped_action_pin(action)}')\n", [1], [1], []),
    ("test_consistency_checks:23", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert steps[0]['uses'] == (\n+    f\"actions/checkout@{_shipped_action_pin('actions/checkout')}\")\n", [1], [1], []),
    ("test_consistency_checks:24", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert {line for line in overlay.splitlines() if line.startswith(\"cp \")} == {\n+    f'cp \"{download[\"with\"][\"path\"]}/{filename}\" {target}'\n+    for filename, target in [\n+        (\"pricing.json\", \"src/pricing.json\"),\n+        (\"constants.py\", \"backend/constants.py\"),\n+        (\"pricing_notices.json\", \"src/pricing_notices.json\"),\n+    ]\n+}\n", [1], [1], []),
    ("test_consistency_checks:25", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert outputs == {f'{raw[\"with\"][\"path\"]}/{filename}' for filename in\n+    [\"pricing.json\", \"constants.py\", \"pricing_notices.json\", \"commit-msg.txt\", \"base.txt\"]}\n", [1], [1], []),
    ("test_consistency_checks:26", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+with_ = scorecard_step.get(\"with\") or {}\n+assert (upload.get(\"with\") or {}).get(\"sarif_file\") == with_[\n+    \"results_file\"], (\n+    \"the SARIF upload reads a different file than the analysis wrote\")\n", [2], [2], []),
    ("test_consistency_checks:27", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert str(upload[\"uses\"]).partition(\"@\")[2] in shas, (\n+    \"scorecard's SARIF upload and codeql.yml's pins disagree: \"\n+    f\"{upload['uses']} against {sorted(shas)}\")\n", [1], [1], []),
    ("test_consistency_checks:28", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+expect(download.with?.[\"run-id\"]).toBe(`\\${{ steps.${awaiter.id}.outputs.run_id }}`);\n", [1], [1], []),
    ("test_consistency_checks:29", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+self.assertEqual([step[\"uses\"] for step in steps if \"uses\" in step],\n+    [downloads[0][\"uses\"]])\n", [1], [1], []),
    ("test_exemptions_do_not_hide_nearby_pins:30", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert checkout['with']['persist-credentials'] is True\n", [], [], [1]),
    ("test_exemptions_do_not_hide_nearby_pins:31", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+expect(step.with.cache).toBe(false);\n", [1], [], []),
    ("test_exemptions_do_not_hide_nearby_pins:32", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+name = 'tests.yml'\n+steps = _workflow(name)['steps']\n+assert steps[0]['uses'] == 'actions/checkout@v4'\n", [3], [], []),
    ("test_exemptions_do_not_hide_nearby_pins:33", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+PIN = 'v4'\n+assert step['uses'] == f'actions/checkout@{PIN}'\n", [2], [], []),
    ("test_exemptions_do_not_hide_nearby_pins:34", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+with_ = step.get('with') or {}\n+assert with_.get('fetch-depth') == '2'\n", [], [], [2]),
    ("test_full_history_invariant:35", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert checkout['with'].get('fetch-depth') == '0', checkout\n", [1], [], []),
    ("test_history_and_credential_invariants:36", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert checkout['with']['fetch-depth'] == '0'\n", [1], [], []),
    ("test_history_and_credential_invariants:37", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert checkout.get('with', {}).get('fetch-depth') == 0\n", [1], [], []),
    ("test_history_and_credential_invariants:38", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert checkout['with']['persist-credentials'] is False\n", [], [], [1]),
    ("test_history_values_are_not_boolean_values:39", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert checkout['with']['fetch-depth'] is False\n", [1], [], []),
    ("test_history_values_are_not_boolean_values:40", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert checkout['with']['persist-credentials'] == 0\n", [], [], [1]),
    ("test_parser_fixture_assertions:41", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+text = ('jobs:\\n'\n+        '  actionlint:\\n')\n+steps = module.workflow_steps(text, 'w.yml')['actionlint']\n+assert steps[0]['uses'] == 'actions/checkout@1111111111111111111111111111111111111111'\n", [4], [4], []),
    ("test_parser_fixture_assertions:42", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+text = ('jobs:\\n'\n+        '  actionlint:\\n')\n+steps = module.workflow_steps(text, 'w.yml')['actionlint']\n+assert steps[1]['uses'] == './'\n", [4], [4], []),
    ("test_parser_fixture_assertions:43", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+text = ('jobs:\\n'\n+        '  actionlint:\\n')\n+steps = module.workflow_jobs(text, 'w.yml')['actionlint'].steps\n+assert steps[0]['uses'] == 'actions/checkout@1111111111111111111111111111111111111111'\n", [4], [4], []),
    ("test_parser_fixture_assertions:44", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+text = ('jobs:\\n'\n+        '  actionlint:\\n')\n+steps = module.workflow_jobs(text, 'w.yml')['actionlint'].steps\n+assert steps[1]['uses'] == './'\n", [4], [4], []),
    ("test_parser_fixture_assertions:45", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+text = ('jobs:\\n'\n+        '  actionlint:\\n')\n+steps = module.workflow_steps(text, 'w.yml')['actionlint']\n+assert steps[0]['uses'] == 'actions/checkout@1111111111111111111111111111111111111111'\n", [4], [4], []),
    ("test_parser_fixture_assertions:46", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+text = ('jobs:\\n'\n+        '  actionlint:\\n')\n+steps = module.workflow_steps(text, 'w.yml')['actionlint']\n+assert steps[1]['uses'] == './'\n", [4], [4], []),
    ("test_pin_sync_fixture_output:47", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert f'{reviewed} # v1.0.1' in result.stdout, result.stdout\n", [1], [1], []),
    ("test_pin_sync_fixture_output:48", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert f'{divergent} # v1.0.1' in result.stdout, result.stdout\n", [1], [1], []),
    ("test_pin_sync_fixture_output:49", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert f'{reviewed} # v1.0.1' in result.stdout, result.stdout\n", [1], [1], []),
    ("test_pin_sync_fixture_output:50", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert f'{reviewed} # v1.1.0' in result.stdout, result.stdout\n", [1], [1], []),
    ("test_quoted_brackets_do_not_join_assertions:51", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert '${{' not in steps[index]['run'], (\n+    'the pin-sync step must not interpolate expressions: zizmor flags '\n+    'script injection through ${{ }} in run blocks')\n+assert names[index + 1] == 'Install actionlint', (\n+    'the pin-sync step must precede Install actionlint')\n+assert step['uses'] == 'actions/checkout@v4'\n", [1], [1], [6]),
    ("test_security_identity_and_absence:52", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+self.assertEqual(\n+    [step[\"uses\"].split(\"@\", maxsplit=1)[0]\n+     for step in self.steps if \"uses\" in step],\n+    [\"actions/download-artifact\"])\n", [1], [1], []),
    ("test_security_identity_and_absence:53", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert not [s for s in self.push[\"steps\"]\n+    if s.get(\"uses\", \"\").startswith(\"actions/checkout@\")], (\n+    \"the push job runs no repository code: no checkout step\")\n", [1], [1], []),
    ("test_security_identity_and_absence:54", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+expect(\n+    actions.map((step) => step.uses!.replace(/@[0-9a-f]{40}$/, \"@\")),\n+    \"the push job's only action is the checkout\",\n+).toEqual([\"actions/checkout@\"]);\n", [1], [1], []),
    ("test_security_identity_and_absence:55", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+expect(steps.some((step) => /^actions\\/checkout(?:@|$)/.test(step.uses ?? \"\"))).toBe(false);\n", [1], [1], []),
    ("test_uses_in_assertion_messages_only:56", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+expect(\n+    effective(step, key),\n+    `step \"${step.name ?? step.uses}\" runs pnpm and must resolve ${key} from the workflow, ` +\n+        \"not from a value its own env block shadows\",\n+).toBe(value);\n", [1], [1], []),
    ("test_uses_in_assertion_messages_only:57", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+expect(\n+    step[\"continue-on-error\"],\n+    `${step.name ?? step.uses ?? \"a step\"} must not tolerate failure`,\n+).toBeUndefined();\n", [1], [1], []),
    ("test_with_alias_miss:58", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+with_ = scorecard_step.get(\"with\") or {}\n+assert with_.get(\"results_format\") == \"sarif\"\n", [], [], [2]),
    ("test_with_dictionary_misses:59", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert checkout['with'] == {'ref': '${{ github.sha }}',\n+    'fetch-depth': '0',\n+    'persist-credentials': 'false'}, (\n+    'the checkout must be the exact merge the coverage report describes')\n", [], [], [1]),
    ("test_with_dictionary_misses:60", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert steps[0]['with'] == {'fetch-depth': '0', 'persist-credentials': 'false'}\n", [], [], [1]),
    ("test_workflow_call_structure:61", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert Path(caller.get('uses', '').split('@', 1)[0]).name != 'actionlint.yml'\n", [1], [1], []),
    ("py startswith version", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert step['uses'].startswith('actions/checkout@v4')\n", [1], [], []),
    ("py count version", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert wf_text.count('actions/checkout@v4') == 1\n", [1], [], []),
    ("py assertTrue compare", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+self.assertTrue(step['uses'] == 'actions/checkout@v4')\n", [1], [], []),
    ("py and-joined pin", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert step['uses'] == 'actions/checkout@v4' and step['with']['ref'] == 'main'\n", [1], [], []),
    ("py and-joined with", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert step['with']['ref'] == 'main' and step['with']['node-version'] == '20'\n", [], [], []),
    ("py version in Attribute", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+self.assertIn('actions/checkout@v4', self.workflow)\n", [1], [], []),
    ("py version in Call", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert 'comment-ratchet@v1' in (ROOT / '.github/workflows/c.yml').read_text()\n", [1], [], []),
    ("py version in attr assert", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert '# v1.0.0' in self.text\n", [1], [], []),
    ("py format call", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert step['uses'] == 'actions/checkout@{}'.format('v4')\n", [1], [], []),
    ("py identity filter then full pin", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert [s['uses'] for s in steps if s['uses'].split('@')[0] == 'actions/checkout'] == ['actions/checkout@v4']\n", [1], [], []),
    ("py tuple identity+pin", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert (step['uses'].split('@')[0], step['uses']) == ('actions/checkout', 'actions/checkout@v4')\n", [1], [], []),
    ("py fixture from attribute", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+text = self.workflow_text\n+steps = module.workflow_steps(text, 'ci.yml')\n+assert steps[0]['uses'] == 'actions/checkout@v4'\n", [3], [], []),
    ("py fixture rebound by for", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+text = 'jobs: {}'\n+steps = module.workflow_steps(text, 'w.yml')\n+for steps in real.values():\n+    assert steps[0]['uses'] == 'actions/checkout@v4'\n", [4], [], []),
    ("py fixture rebound by annassign", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+text = 'jobs: {}'\n+steps = module.workflow_steps(text, 'w.yml')\n+steps: list = load_workflow('ci.yml')['steps']\n+assert steps[0]['uses'] == 'actions/checkout@v4'\n", [4], [], []),
    ("py fixture rebound by tuple", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+text = 'jobs: {}'\n+steps = module.workflow_steps(text, 'w.yml')\n+steps, jobs = load('ci.yml')\n+assert steps[0]['uses'] == 'actions/checkout@v4'\n", [4], [], []),
    ("py fetch-depth 0 any step", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert step['with']['fetch-depth'] == 0\n", [1], [], []),
    ("py not-equal pin", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert not step['uses'] != 'actions/checkout@v4'\n", [1], [], []),
    ("py with ref literal", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert step['with']['ref'] == 'main'\n", [1], [], []),
    ("py plain name pin", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+PIN = 'actions/checkout@v4'\n+assert step['uses'] == PIN\n", [], [], []),
    ("js template literal-held", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+const PIN = 'v4';\n+expect(step.uses).toBe(`actions/checkout@${PIN}`);\n", [2], [], []),
    ("js template with input", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+expect(step.with['node-version']).toBe(`${NODE}.x`);\n", [1], [], []),
    ("js uses toBe", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+expect(step.uses).toBe('actions/checkout@v4');\n", [1], [], []),
    ("js with toEqual", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+expect(step.with).toEqual({ ref: 'main' });\n", [1], [], []),
    ("js some toBe false with pin", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+expect(steps.some((s) => s.with?.ref === 'main')).toBe(false);\n", [1], [], []),
    ("js msg holds key, subject is pin", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+expect(step.with.ref, `${step.uses}`).toBe('main');\n", [1], [], []),
    ("revision index", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert step['uses'].split('@')[1] == 'v4'\n", [1], [], []),
    ("non revision separator", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+assert step['uses'].split('/')[0] == 'actions'\n", [1], [], []),
    ("unknown parser argument", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+steps = module.workflow_steps(SHIPPED_TEXT, 'ci.yml')['ci']\n+assert steps[0]['uses'] == 'actions/checkout@v4'\n", [2], [], []),
    ("non string literal fixture", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+text = 0\n+steps = module.workflow_steps(text, 'ci.yml')\n+assert steps[0]['uses'] == 'actions/checkout@v4'\n", [3], [], []),
    ("fixture rebound by walrus", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -1,1 +1,9 @@\n+text = 'jobs: {}'\n+steps = module.workflow_steps(text, 'w.yml')\n+if (steps := load('ci.yml')):\n+    assert steps[0]['uses'] == 'actions/checkout@v4'\n", [4], [], []),
    ("added fixture crosses hunks", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -10,1 +10,3 @@ def test_parser():\n+    text = 'jobs: {}'\n+    steps = module.workflow_steps(text, 'w.yml')\n@@ -300,1 +302,2 @@ def test_shipped():\n     def test_shipped():\n+        assert steps[0]['uses'] == 'actions/checkout@v4'\n", [303], [], []),
    ("context fixture crosses hunks", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -10,1 +10,3 @@ def test_parser():\n     text = 'jobs: {}'\n+    steps = module.workflow_steps(text, 'w.yml')\n@@ -300,1 +302,2 @@ def test_shipped():\n     def test_shipped():\n+        assert steps[0]['uses'] == 'actions/checkout@v4'\n", [303], [], []),
    ("context alias crosses hunks", "diff --git a/tests/test_workflow.py b/tests/test_workflow.py\n--- a/tests/test_workflow.py\n+++ b/tests/test_workflow.py\n@@ -10,1 +10,2 @@ def test_a():\n+    cfg = step[\"with\"]\n@@ -300,1 +301,2 @@ def test_b():\n     cfg = load_config()\n+    assert cfg[\"mode\"] == \"fast\"\n", [], [], []),
]


class FixRoundOne(unittest.TestCase):
    def screen(self, source):
        return mod.findings(diff('tests/test_wf.py', *source.splitlines()), (), lambda p: True)

    def test_base_pins_without_version_reference_tokens(self):
        for source in ("assert step['uses'] == 'actions/' + 'checkout@'",
                       "assert step['uses'] == 'actions/{}'.format('checkout')"):
            with self.subTest(source=source):
                self.assertEqual(len(self.screen(source)), 1)

    def test_unittest_dictionary_pin_detection(self):
        self.assertEqual(len(self.screen("self.assertEqual(step['with'], {'persist-credentials': 'false', 'ref': 'main'})")), 1)

    def test_unknown_computations_do_not_prove_consistency(self):
        for source in (
            "PIN = 'v4'\nassert step['uses'] == 'checkout@' + PIN",
            "assert step['uses'] == f'checkout@{unknown()}'",
            "pin = unknown()\nassert step['uses'] == f'checkout@{pin}'",
        ):
            with self.subTest(source=source):
                self.assertEqual(len(self.screen(source)), 1)

    def test_composed_literals_in_input_dictionaries_still_pin(self):
        self.assertEqual(len(self.screen("assert step['with'] == {'persist-credentials': 'false', 'ref': 'ma' + 'in'}")), 1)

    def test_python_diagnostic_arguments_are_not_pin_operands(self):
        for source in ("self.assertIn('scan', self.workflow, 'actions/checkout@v4')",
                       "self.assertIn('scan', self.workflow, msg='actions/checkout@v4')"):
            with self.subTest(source=source):
                self.assertEqual(self.screen(source), [])

    def test_unittest_keyword_operands_still_pin(self):
        self.assertEqual(len(self.screen("self.assertEqual(first=step['uses'], second='checkout@v4')")), 1)

    def test_attribute_and_get_consistency_accesses(self):
        for access in ("step.uses", "step.get('uses')"):
            with self.subTest(access=access):
                self.assertEqual(self.screen(f"assert {access} == f'checkout@{{_shipped_action_pin(action)}}'"), [])

    def test_revision_index_still_pins(self):
        self.assertEqual(len(self.screen("assert step['uses'].split('@')[1] == 'v4'")), 1)

    def test_non_revision_separator_still_pins(self):
        self.assertEqual(len(self.screen("assert step['uses'].split('/')[0] == 'actions'")), 1)

    def test_identity_source_must_be_uses(self):
        self.assertEqual(len(self.screen("assert other.split('@', maxsplit=step['uses'])[0] == 'checkout'")), 1)

    def test_identity_filter_must_not_pin_revision(self):
        source = "assert [s['uses'].split('@')[0] for s in steps if s['uses'] == 'checkout@v4'] == ['checkout']"
        self.assertEqual(len(self.screen(source)), 1)

    def test_unknown_fixture_argument_still_pins(self):
        self.assertEqual(len(self.screen("steps = module.workflow_steps(SHIPPED_TEXT, 'ci.yml')\n"
                                         "assert steps[0]['uses'] == 'actions/checkout@v4'")), 1)

    def test_fixture_string_label_requires_a_string_literal(self):
        for value in ('self.workflow_text', '42', 'None'):
            with self.subTest(value=value):
                self.assertEqual(len(self.screen(f"text = {value}\n"
                                                 "steps = module.workflow_steps(text, 'ci.yml')\n"
                                                 "assert steps[0]['uses'] == 'actions/checkout@v4'")), 1)

    def test_incomplete_store_fragment_clears_provenance(self):
        source = ("text = 'jobs: {}'\nsteps = module.workflow_steps(text, 'w.yml')\n"
                  "for steps in (\n    real['steps'],\n):\n"
                  "    assert steps[0]['uses'] == 'actions/checkout@v4'")
        self.assertEqual(len(self.screen(source)), 1)

    def test_hunk_boundary_clears_fixture_without_a_context_definition(self):
        patch = ("diff --git a/tests/test_wf.py b/tests/test_wf.py\n"
                 "--- a/tests/test_wf.py\n+++ b/tests/test_wf.py\n"
                 "@@ -10,1 +10,3 @@ def test_parser():\n"
                 "+    text = 'jobs: {}'\n+    steps = module.workflow_steps(text, 'ci.yml')\n"
                 "@@ -300,1 +302,2 @@ def test_real():\n"
                 "     steps = load_workflow('ci.yml')\n"
                 "+    assert steps[0]['uses'] == 'actions/checkout@v4'\n")
        self.assertEqual([n for _, n, _ in mod.findings(patch, (), lambda p: True)], [303])

    def test_context_definition_clears_fixture_within_a_hunk(self):
        patch = ("diff --git a/tests/test_wf.py b/tests/test_wf.py\n"
                 "--- a/tests/test_wf.py\n+++ b/tests/test_wf.py\n@@ -1,3 +1,5 @@\n"
                 "+    text = 'jobs: {}'\n+    steps = module.workflow_steps(text, 'ci.yml')\n"
                 " def test_real():\n+    assert steps[0]['uses'] == 'actions/checkout@v4'\n")
        self.assertEqual([n for _, n, _ in mod.findings(patch, (), lambda p: True)], [4])

    def test_fixture_names_do_not_exempt_other_operands(self):
        for tail in (
            "assert (steps[0]['uses'], real['uses']) == ('./', 'checkout@v4')",
            "mixed = choose_real(steps)\nassert mixed['uses'] == 'checkout@v4'",
        ):
            with self.subTest(tail=tail):
                source = "text = 'jobs: {}'\nsteps = module.workflow_steps(text, 'w.yml')\n" + tail
                self.assertEqual(len(mod.findings(diff('tests/test_wf.py', *source.splitlines()), (), lambda p: True)), 1)

    def test_classifier_fixture_must_be_the_compared_operand(self):
        for source in (
            "assert (classify({'uses': 'fixture@v1'}), step['uses']) == (True, 'checkout@v4')",
            "assert classify({'uses': 'fixture@v1'}) == step['uses']",
            "assert (classify({'uses': 'fixture@v1'}), self.workflow) == (True, 'checkout@v4')",
        ):
            with self.subTest(source=source):
                self.assertEqual(len(self.screen(source)), 1)

    def test_differential_fixture_corpus(self):
        old, actual, removed, gained = set(), set(), set(), set()
        for label, patch, base, exemptions, additions in FIX1_CORPUS:
            with self.subTest(shape=label):
                observed = {n for _, n, _ in mod.findings(patch, {SHA}, lambda p: True)}
                self.assertEqual(observed, (set(base) - set(exemptions)) | set(additions))
                old.update((label, n) for n in base)
                actual.update((label, n) for n in observed)
                removed.update((label, n) for n in exemptions)
                gained.update((label, n) for n in additions)
        self.assertEqual(actual & old, old - removed)
        self.assertEqual(actual - old, gained)
        print(f'Differential: {len(FIX1_CORPUS)} shapes; {len(old)} base flags; '
              f'{len(removed)} explicit exemptions; {len(gained)} retained additions; '
              f'{len(actual)} resulting flags')


class SettledPinBoundary(unittest.TestCase):
    def screen(self, source, reader="text = (ROOT / '.github/workflows/ci.yml').read_text()"):
        return [(n - 1, text) for _, n, text in mod.findings(
            diff('tests/test_workflow.py', reader, *source.splitlines()))]

    def test_single_file_credentials_and_history(self):
        # claudit 36bc0c76: scorecard:404, gate_freshness:397-398,
        # secrets:97,100, panel_layout_js:657; gh-widgets origin/main:737.
        for source in (
            'assert (checkout.get("with") or {}).get("persist-credentials") in ("false", False)',
            'assert "fetch-depth: 0" in text',
            'assert "persist-credentials: false" in text',
            'assert "run-id: ${{ github.event.workflow_run.id }}" in text',
            'assert "fetch-depth: 0" in block, "scan history"',
            'assert "persist-credentials: false" in block, "drop token"',
            'assert checkout["with"] == {"persist-credentials": False}',
            'self.assertEqual(\n    _child_value(step["with"], "persist-credentials"),\n    "false")',
            'expect(step.with["persist-credentials"]).toBe(false);',
        ):
            with self.subTest(source=source):
                self.assertEqual([n for n, _ in self.screen(source)], [1])
        for source in (
            '# assert "fetch-depth: 0" in text',
            'assert text, "persist-credentials: false"',
            'assert fetch_depth_0',
            'assert "fetch-depth: 0" not in text',
        ):
            with self.subTest(decoy=source):
                self.assertEqual(self.screen(source), [])
        self.assertEqual(self.screen('assert "fetch-depth: 0" in text',
                                     "text = Path('fixture.txt').read_text()"), [])

    def test_enumerated_security_invariants_only(self):
        for paths in (
            '(ROOT / ".github/workflows").glob("*.y*ml")',
            '(ROOT / ".github/actions").rglob("action.yml")',
        ):
            for check in (
                'assert step["with"]["persist-credentials"] is False',
                'assert step["with"]["run-id"] == "${{ github.event.workflow_run.id }}"',
            ):
                source = ('seen = 0\nfor path in ' + paths + ':\n'
                          '    wf = yaml.safe_load(path.read_text())\n'
                          '    for step in wf["steps"]:\n'
                          '        seen += 1\n        ' + check + '\nassert seen > 0')
                with self.subTest(paths=paths, check=check):
                    self.assertEqual(self.screen(source), [])
                    single = source.replace(paths, '[ROOT / ".github/workflows/ci.yml"]')
                    self.assertEqual([n for n, _ in self.screen(single)], [6])
                    unrelated = source.replace(check, 'assert step["with"]["fetch-depth"] == 0')
                    self.assertEqual([n for n, _ in self.screen(unrelated)], [6])
                    wrong = source.replace(check, 'assert step["with"]["persist-credentials"] is True')
                    self.assertEqual([n for n, _ in self.screen(wrong)], [6])
        self.assertEqual([n for n, _ in self.screen('''for path in [".github/workflows/ci.yml"]:
    assert step["with"]["ref"] == "main", "glob persist-credentials"
''')], [2])
        # pr-gate origin/main test_workflows:1178: sorted enumeration, get access.
        self.assertEqual(self.screen('''for path in sorted((ROOT / '.github/workflows').glob('*.yml')):
    for job in _workflow(path.name)['jobs'].values():
        for step in job['steps']:
            assert step.get('with', {}).get('persist-credentials') == 'false', path
assert families
'''), [])

        # Directory aliases seen in prior screenings
        # (claudit workflow_ci_gate:563 and gh-widgets ci:300).
        # The forward alias forces more than a single assignment-resolution pass.
        directories = (
            ('WORKFLOWS = ROOT / ".github/workflows"\n', 'WORKFLOWS.glob("*.yml")'),
            ('ROOT = Path("/repo")\nWORKFLOWS = ROOT / ".github/workflows"\n', 'WORKFLOWS.glob("*.yml")'),
            ('WORKFLOWS = A / "workflows"\nA = ROOT / ".github"\n', 'WORKFLOWS.glob("*.yaml")'),
            ('A = ROOT / ".github"\nWORKFLOWS = A / "workflows"\n', 'WORKFLOWS.glob("*.y*ml")'),
            ('', 'Path(".github/workflows").glob("*.yml")'),
            ('', 'ROOT.glob(".github/workflows/*.yml")'),
            ('', 'ROOT.glob(".github/workflows/*.yaml")'),
            ('', 'ROOT.glob(".github/workflows/*.y*ml")'),
        )
        assertion = 'self.assertIs(step["with"]["persist-credentials"], False)'
        for prefix, iterator in directories:
            for wrap in ('{}', 'sorted({})', 'list({})', 'sorted(list({}))'):
                source = prefix + ('def test_all(self):\n'
                                   f'    for path in {wrap.format(iterator)}:\n'
                                   '        for step in yaml.safe_load(path.read_text())["jobs"]["j"]["steps"]:\n'
                                   f'            {assertion}')
                with self.subTest(iterator=iterator, wrap=wrap, aliases=prefix):
                    self.assertEqual(self.screen(source), [])
                    single = source.replace(wrap.format(iterator), '(ROOT / ".github/workflows/ci.yml",)')
                    self.assertEqual(len(self.screen(single)), 1)
        for check in (
            'self.assertIs(step["with"]["persist-credentials"], False)',
            'self.assertEqual(step["with"]["persist-credentials"], "false")',
            'self.assertFalse(step["with"]["persist-credentials"])',
            'self.assertTrue(step["with"]["persist-credentials"] is False)',
            'self.assertIn(step["with"]["persist-credentials"], ("false", False))',
            'self.assertNotIn("persist-credentials: true", path.read_text())',
            'self.assertEqual(step["with"]["run-id"], "${{ github.event.workflow_run.id }}")',
            'self.assertIn("persist-credentials: false", path.read_text())',
        ):
            source = ('for path in (ROOT / ".github/workflows").glob("*.yml"):\n'
                      '    for step in yaml.safe_load(path.read_text())["steps"]:\n'
                      '        ' + check)
            with self.subTest(unittest=check):
                self.assertEqual(self.screen(source), [])
        for prefix, iterator in (
            ('WORKFLOWS = ROOT / "tests/fixtures/workflows"\n', 'sorted(WORKFLOWS.glob("*.yml"))'),
            ('WORKFLOWS = ROOT / ".github/workflows"\nCI = WORKFLOWS / "ci.yml"\n', 'list(CI.glob("*.yml"))'),
            ('A = ROOT / ".github/workflows/a.yml"\nB = ROOT / ".github/workflows/b.yml"\n', '(A, B)'),
            ('WORKFLOWS = ROOT / ".github/workflows"\n', 'WORKFLOWS.glob("ci*.yml")'),
            ('', 'ROOT.glob(".github/workflows/ci*.yml")'),
            ('', 'Path("tests/fixtures/workflows").glob("*.yml")'),
            ('A = B\nB = A\n', 'A.glob("*.yml")'),
        ):
            source = prefix + (f'for path in {iterator}:\n'
                               '    for step in yaml.safe_load(path.read_text())["steps"]:\n'
                               '        ' + assertion)
            with self.subTest(not_enumeration=iterator, aliases=prefix):
                self.assertEqual(len(self.screen(source)), 1)
        unrelated = ('for path in list(Path(".github/workflows").glob("*.yml")):\n'
                     '    for step in yaml.safe_load(path.read_text())["steps"]:\n'
                     '        self.assertIs(outside["with"]["persist-credentials"], False)')
        self.assertEqual(len(self.screen(unrelated)), 1)
        self.assertEqual(len(self.screen(assertion)), 1)
        shadowed = ('WORKFLOWS = ROOT / ".github/workflows"\ndef test_other():\n'
                    '    WORKFLOWS = Path("tests/fixtures/workflows")\n'
                    '    for path in WORKFLOWS.glob("*.yml"):\n'
                    '        for step in yaml.safe_load(path.read_text())["steps"]:\n'
                    '            ' + assertion)
        self.assertEqual(len(self.screen(shadowed)), 1)

        # Same --pr hunk as the review, plus a dedented body and fail-closed fragment.
        body = ['def test_all():',
                '    for path in (ROOT / ".github/workflows").glob("*.yml"):',
                '        for step in yaml.safe_load(path.read_text())["jobs"]["j"]["steps"]:',
                '            assert step["with"]["persist-credentials"] is False']
        for context in (['', ''], ['    assert other == 1', '', '']):
            for loop in (body, [line.replace('.glob("*.yml")', '.glob("ci*.yml")') for line in body]):
                patch = ('+++ b/tests/test_hunk.py\n@@ -10,3 +10,7 @@\n'
                         + '\n'.join(' ' + line for line in context) + '\n'
                         + '\n'.join('+' + line for line in loop) + '\n')
                with self.subTest(context=context, loop=loop[1]):
                    found = mod.findings(patch, (), lambda p: True)
                    self.assertEqual([n for _, n, _ in found], [] if loop is body else [10 + len(context) + 3])
        for iterator, wanted in (('(ROOT / ".github/workflows").glob("*.yml")', []),
                                 ('[ROOT / ".github/workflows/ci.yml"]', [13])):
            patch = ('+++ b/tests/test_hunk.py\n@@ -10,3 +10,4 @@\n'
                     '     seen = 0\n'
                     f'+    for path in {iterator}:\n'
                     '+        for step in yaml.safe_load(path.read_text())["steps"]:\n'
                     '+            assert step["with"]["persist-credentials"] is False\n')
            self.assertEqual([n for _, n, _ in mod.findings(patch, (), lambda p: True)], wanted)
        incomplete = ('+++ b/tests/test_hunk.py\n@@ -10,3 +10,4 @@\n'
                      '     for path in (ROOT / ".github/workflows").glob(\n'
                      '+        assert step["with"]["persist-credentials"] is False\n')
        self.assertEqual([n for _, n, _ in mod.findings(incomplete, (), lambda p: True)], [11])

    def test_split_workflow_path_couples_but_prose_does_not(self):
        line = 'assert checkout["with"] == {"persist-credentials": False}'
        # claudit 36bc0c76 panel_layout_js:639-657, only the relevant reads.
        self.assertEqual(len(self.screen(line, 'workflow = (ROOT / ".github" / "workflows" / "panel-layout.yml").read_text()')), 1)
        for decoy in (
            '# ROOT / ".github" / "workflows"',
            'assert True, \'ROOT / ".github" / "workflows"\'',
            'github_workflows = True',
        ):
            self.assertEqual(self.screen(decoy + '\n' + line, 'text = Path("fixture.txt").read_text()'), [])

    def test_literal_expression_and_derived_wiring(self):
        # claudit postgres:80-81,91-92; cached_image:85-87; ci:328,332,337;
        # ai-researcher ratchet_push:111-112.
        for line in (
            'assert cached["ref"] == "${{ steps.image.outputs.ref }}"',
            'assert cached["key"] == "${{ steps.image.outputs.key }}"',
            'assert cached["key"] == "${{ inputs.key }}"',
            'assert cached["path"] == "${{ runner.temp }}/${{ inputs.archive }}"',
            'assert cached["ref"] == cached["tag"] == "${{ steps.base.outputs.ref }}"',
            'assert cached["fallback-ref"] == "${{ steps.base.outputs.fallback }}"',
            'assert cached["key"] == "${{ steps.base.outputs.key }}"',
            'assert cached.get("languages") == "${{ matrix.language }}"',
            'assert cached.get("build-mode") == "${{ matrix.build-mode }}"',
            'assert cached.get("category") == "/language:${{ matrix.language }}"',
            'assert cached["name"] == "${{ steps.list.outputs.artifact }}"',
            'assert cached["run-id"] == "${{ github.event.workflow_run.id }}"',
            'assert cached["token"] == "${{ secrets.GITHUB_TOKEN }}"',
            'assert cached["ref"].replace(" ", "") == "${{steps.image.outputs.ref}}"',
            'assert re.sub(r"\\s+", "", cached["ref"]) == "${{steps.image.outputs.ref}}"',
        ):
            with self.subTest(line=line):
                self.assertEqual([n for n, _ in self.screen('cached = step["with"]\n' + line)], [2])
        derived = '''wf = yaml.safe_load(WORKFLOW.read_text())
steps = wf["jobs"]["build"]["steps"]
image = next(s for s in steps if s.get("id") == "image")
expected = "${{ steps." + image["id"] + ".outputs.ref }}"
assert "ref=" in image["run"]
assert step["with"]["ref"] == expected
assert step["with"]["ref"] == f"${{{{ steps.{image['id']}.outputs.ref }}}}"
'''
        self.assertEqual(self.screen(derived), [])
        # Review probe: the workflow reader is module-level, the step id is local.
        self.assertEqual(self.screen('''def test_wire():
    steps = yaml.safe_load(text)["jobs"]["j"]["steps"]
    step_id = next(s["id"] for s in steps if "id" in s)
    assert steps[1]["with"]["ref"] == f"${{{{ steps.{step_id}.outputs.ref }}}}"
'''), [])
        self.assertEqual(len(self.screen('''def test_wire(text):
    steps = yaml.safe_load(text)["jobs"]["j"]["steps"]
    step_id = next(s["id"] for s in steps if "id" in s)
    assert steps[1]["with"]["ref"] == f"${{{{ steps.{step_id}.outputs.ref }}}}"
''')), 1)
        for decoy in ('# expected = image["id"]', 'expected = "${{ steps.image.outputs.ref }}"'):
            source = decoy + '\nassert step["with"]["ref"] == "${{steps.image.outputs.ref}}", "derived image[id]"'
            self.assertEqual([n for n, _ in self.screen(source)], [2])

    def test_inline_parser_fixture_and_shipped_workflow(self):
        # pr-gate:595-610, gh-widgets:586-601, ai-researcher:901-916.
        text = """text = ('name: w\\n'
        'jobs:\\n'
        '  actionlint:\\n'
        '    # a comment inside the job block\\n'
        '\\n'
        '    runs-on: ubuntu-latest\\n'
        '    steps:\\n'
        '      - run: |\\n'
        '          echo one\\n'
        '        name: renamed\\n'
        'permissions:\\n'
        '  contents: read\\n')
"""
        for parser, select in (("module.workflow_steps(text, 'w.yml')", "jobs['actionlint']"),
                               ("module.workflow_jobs(text, 'w.yml')", "jobs['actionlint'].steps")):
            source = text + f'jobs = {parser}\nsteps = {select}\nassert steps[0]["uses"] is None'
            with self.subTest(parser=parser):
                self.assertEqual(self.screen(source), [])
                real = source.replace(text, 'text = WORKFLOW.read_text()\n')
                self.assertEqual([n for n, _ in self.screen(real)], [4])
        self.assertEqual([n for n, _ in self.screen(
            '# module.workflow_steps(text, "w.yml")\nassert steps[0]["uses"] is None')], [2])

    def test_step_order_selectors_do_not_pin_inputs_or_revisions(self):
        # ai-researcher:319 and claudit:539, with names/input presence selectors.
        for source in (
            '''assert steps.index(stage) > next(
    i for i, s in enumerate(steps)
    if s.get("uses", "").startswith("Nitjsefnie-Actions/comment-ratchet@"))''',
            '''assert all(i < fetch for i, step in enumerate(steps)
    if "python-version" in step.get("with", {}))''',
            '''assert all(i < fetch for i, step in enumerate(steps)
    if "cache" in step.get("with", {}))''',
            '''assert steps.index(stage) > next(i for i, s in enumerate(steps)
    if s.get("name") == "Install Python")''',
        ):
            with self.subTest(source=source):
                self.assertEqual(self.screen(source), [])
        for source in (
            '''assert steps.index(stage) > next(i for i, s in enumerate(steps)
    if re.match(r"actions/setup-python@v5", s["uses"]))''',
            '''assert steps.index(stage) > next(i for i, s in enumerate(steps)
    if fnmatch(s.get("uses", ""), "*@v5"))''',
            '''assert steps.index(stage) > next(i for i, s in enumerate(steps)
    if s.get("uses", "") == "actions/setup-python@v5")''',
            '''assert all(i < fetch for i, step in enumerate(steps)
    if step.get("with", {}).get("python-version") == "3.12")''',
            '''assert all(i < fetch for i, step in enumerate(steps)
    if step.get("with", {}).get("python-version") == "3.12"), "order only"''',
        ):
            with self.subTest(pin=source):
                self.assertEqual([n for n, _ in self.screen(source)], [1])

    def test_exemptions_do_not_follow_decoys(self):
        for source in (
            'image = {"id": "fixed"}\nassert step["with"]["ref"] == f"${{{{steps.{image[\'id\']}.outputs.ref}}}}"',
            'assert all(i < fetch for i, step in enumerate(steps) if step["with"]["cache"] == "true")',
            'assert step["with"]["fetch-depth"] > 0',
        ):
            with self.subTest(source=source):
                self.assertEqual(len(self.screen(source)), 1)
        for directory in ('".github/workflows/fixtures"', 'some_workflows'):
            source = f'''for path in (ROOT / {directory}).glob("*.y*ml"):
    wf = yaml.safe_load(path.read_text())
    for step in wf["steps"]:
        assert step["with"]["persist-credentials"] is False
'''
            self.assertEqual([n for n, _ in self.screen(source)], [4])
        self.assertEqual([n for n, _ in self.screen('''for path in (ROOT / ".github/workflows").glob("*.y*ml"):
    wf = yaml.safe_load(FIXED_WORKFLOW.read_text())
    for step in wf["steps"]:
        assert step["with"]["persist-credentials"] is False
''')], [4])
        for source in (
            '// expect(ref).toMatch(/sha256:[a-f0-9]{64}/);',
            'const prose = "expect(ref).toMatch(/sha256:[a-f0-9]{64}/)";',
            'expect(ref, "sha256:[a-f0-9]{64}").toMatch(/ok/);',
            'expect(ref).toMatch(/ok/); // sha256:[a-f0-9]{64}',
            'PINNED_USE = re.compile(r"[0-9a-f]{40}")\ndef test_other():\n    PINNED_USE = re.compile("okay")\n    self.assertRegex(value, PINNED_USE)',
        ):
            with self.subTest(decoy=source):
                self.assertEqual(self.screen(source), [])
        # Regex bindings are file-local, even if the next file names the same pattern.
        patch = (diff('tests/test_a.py', 'PINNED_USE = re.compile(r"[0-9a-f]{40}")')
                 + diff('tests/test_b.py', 'text = Path("Dockerfile").read_text()',
                        'self.assertRegex(value, PINNED_USE)'))
        self.assertEqual(mod.findings(patch), [])

    def test_pin_shape_regexes_and_consistency(self):
        # claudit postgres:75,77,114 and gh-widgets coverage_ratchet:724.
        compiled = '''PINNED_USE = re.compile(r"^\\s*-?\\s*uses:\\s*[\\w.-]+/[\\w.-]+@[0-9a-f]{40}"
                        r"\\s+#\\s+v[0-9][0-9A-Za-z.\\-]*$")
self.assertRegex(line, PINNED_USE)'''
        for reader in ("text = Path('.github/workflows/ci.yml').read_text()",
                       "text = Path('.github/actions/db/action.yml').read_text()",
                       "text = Path('Dockerfile').read_text()",
                       "text = Path('compose.yml').read_text()"):
            for source in (compiled,
                           compiled.replace("self.assertRegex", "def test_pin():\n    self.assertRegex"),
                           'assert re.fullmatch(r"sha256:[0-9a-f]{64}", digest)',
                           'assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", ref), ref',
                           'assert re.fullmatch(r"[0-9a-f]{40}", sha)',
                           'assert re.match(r"^[a-f0-9]{40}$", sha)',
                           'assert re.search(r"[0-9a-f]{40}", sha)',
                           'assert re.compile(r"[0-9a-f]{40}").fullmatch(sha)',
                           'pattern = re.compile(r"[0-9a-f]{40}")\nassert pattern.fullmatch(sha)'):
                with self.subTest(reader=reader, source=source):
                    self.assertEqual(len(self.screen(source, reader)), 1)
        for source in (
            'assert compose_image.rpartition("@")[2] == digest',
            'assert re.fullmatch(r"[0-9a-f]{64}", data)',
            'assert re.fullmatch(r"[0-9a-f]{39}", data)',
            'assert data, "sha256:[0-9a-f]{64}"',
            '# assert re.fullmatch(r"sha256:[0-9a-f]{64}", digest)',
            'assert sha256_64_hex',
            'assert re.fullmatch(r"ok", value) # sha256:[0-9a-f]{64}',
        ):
            with self.subTest(near_miss=source):
                self.assertEqual(self.screen(source), [])
        self.assertEqual(self.screen('assert re.fullmatch(r"[0-9a-f]{40}", sha)',
                                     "text = Path('fixture.txt').read_text()"), [])


class EnumerationProvenance(unittest.TestCase):
    screen = SettledPinBoundary.screen
    # These fixtures retain the reads and assertions from the two reported refs.
    PR_GATE = '''def test_all(tmp):
    steps_by_path = []
    for path in sorted((ROOT / '.github/workflows').glob('*.yml')):
        for job in _workflow(path.name)['jobs'].values():
            steps_by_path.append((path, job['steps']))
    action_paths = subprocess.run(
        ['git', 'ls-files', '--', 'action.yml', '*/action.yml'],
        cwd=ROOT, check=True, capture_output=True, text=True).stdout.splitlines()
    for name in action_paths:
        path = ROOT / name
        action = yaml.load(path.read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
        steps_by_path.append((path, action['runs']['steps']))
    checkouts = 0
    for path, steps in steps_by_path:
        for step in steps:
            if 'uses' not in step:
                continue
            reference = step['uses']
            action, pin = reference.split('@')
            if action == 'actions/checkout':
                checkouts += 1
                assert step.get('with', {}).get('persist-credentials') == 'false', path
    assert checkouts
'''
    AI_RESEARCHER = '''WORKFLOWS = sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml"))
class WorkflowRunArtifactTests(unittest.TestCase):
    def test_downloads_use_the_triggering_run_id(self):
        downloads = []
        for path in WORKFLOWS:
            workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
            if "workflow_run" not in workflow.get("on", {}):
                continue
            for name, job in workflow["jobs"].items():
                with self.subTest(workflow=path.name, job=name):
                    for entry in job.get("steps", []):
                        if entry.get("uses", "").split("@", 1)[0] != "actions/download-artifact":
                            continue
                        downloads.append(entry)
                        self.assertEqual(
                            "".join(entry.get("with", {}).get("run-id", "").split()),
                            "${{github.event.workflow_run.id}}")
        self.assertTrue(downloads, "no workflow_run artifact downloads found")
'''

    def assert_listed(self, source):
        self.assertEqual(len(self.screen(source)), 1, source)

    def test_live_pr_gate_accumulation_and_near_misses(self):
        self.assertEqual(self.screen(self.PR_GATE), [])
        single = self.PR_GATE.replace("sorted((ROOT / '.github/workflows').glob('*.yml'))",
                                      "[ROOT / '.github/workflows/ci.yml']").replace(
                                          "['git', 'ls-files', '--', 'action.yml', '*/action.yml']",
                                          "['git', 'ls-files', '--', '.github/actions/local/action.yml']")
        self.assert_listed(single)
        filtered = self.PR_GATE.replace("        for job", "        if path.name != 'ci.yml':\n            continue\n        for job")
        self.assert_listed(filtered)
        filtered_actions = self.PR_GATE.replace('    for name in action_paths:',
                                                '    for name in action_paths:\n        if name != "local/action.yml":\n            continue')
        self.assert_listed(filtered_actions)

    def test_live_ai_researcher_binding_and_near_misses(self):
        self.assertEqual(self.screen(self.AI_RESEARCHER), [])
        self.assert_listed(self.AI_RESEARCHER.replace(
            'sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml"))',
            '[REPO_ROOT / ".github/workflows/ci.yml"]'))
        self.assert_listed(self.AI_RESEARCHER.replace(
            '            workflow =', '            if path.name != "ci.yml":\n                continue\n            workflow ='))

    def test_live_cases_in_pr_hunks(self):
        for source in (self.PR_GATE, self.AI_RESEARCHER):
            lines = source.splitlines()
            assertion = next(i for i, s in enumerate(lines) if 'assert step.get' in s or 'self.assertEqual(' in s)
            patch = '+++ b/tests/test_live.py\n@@ -30,1 +30,99 @@\n'
            last = assertion + (2 if source == self.AI_RESEARCHER else 0)
            patch += '\n'.join(('+' if assertion <= i <= last else ' ') + s for i, s in enumerate(lines)) + '\n'
            self.assertEqual(mod.findings(patch, (), lambda p: True), [])
            # A hunk cannot borrow a binding that it does not show.
            if source == self.AI_RESEARCHER:
                missing = patch.replace(' ' + lines[0] + '\n', '')
                self.assertEqual(len(mod.findings(missing, (), lambda p: True)), 1)

    def invariant(self, paths, prefix='', body=''):
        return prefix + f'''for candidate in {paths}:
{body}    parsed = yaml.safe_load(candidate.read_text())
    for job in parsed["jobs"].values():
        for entry in job.get("steps", []):
            assert entry["with"]["persist-credentials"] is False
'''

    def test_sources_and_wrappers_have_narrow_boundaries(self):
        for source in ('(ROOT / ".github/workflows").glob("*.yml")',
                       '(ROOT / ".github/workflows").glob("*.yaml")',
                       '(ROOT / ".github/workflows").rglob("*.y*ml")',
                       'ROOT.glob(".github/workflows/*.yml")',
                       'ROOT.rglob(".github/workflows/*.yaml")',
                       'ROOT.glob(".github/workflows/*.y*ml")',
                       'ROOT.rglob("action.yml")',
                       '(ROOT / ".github/actions").rglob("action.yml")',
                       'subprocess.check_output(["git", "ls-files", "--", "action.yml"], text=True).splitlines()',
                       'subprocess.run(["git", "ls-files", "action.yml"], capture_output=True).stdout.splitlines()'):
            for wrapper in ('{}', 'sorted({})', 'list({})', 'tuple({})'):
                with self.subTest(source=source, wrapper=wrapper):
                    self.assertEqual(self.screen(self.invariant(wrapper.format(source))), [])
        for source in ('(ROOT / ".github/workflows").glob("ci*.yml")',
                       'ROOT.glob(".github/workflows/ci*.yaml")',
                       '(ROOT / "tests/fixtures/.github/workflows").glob("*.yml")',
                       '(ROOT / "tests/fixtures/workflows").glob("*.yml")',
                       '(ROOT / ".github/workflows/ci.yml")',
                       '[ROOT / ".github/workflows/ci.yml"]',
                       'ROOT.glob("action.yml")', 'ROOT.rglob("local/action.yml")',
                       'ROOT.rglob("action.yaml")',
                       '(ROOT / "tests/fixtures").rglob("action.yml")',
                       'subprocess.run(["git", "ls-files", "--", "local/action.yml"]).stdout.splitlines()',
                       'subprocess.run(["git", "ls-files", "--", "*.yml"]).stdout.splitlines()',
                       'subprocess.run(["echo", "git", "ls-files", "action.yml"]).stdout.splitlines()',
                       'mystery((ROOT / ".github/workflows").glob("*.yml"))',
                       'tuple([ROOT / ".github/workflows/ci.yml"])'):
            with self.subTest(near_miss=source):
                self.assert_listed(self.invariant(source))

    def test_module_and_local_bindings_resolve_to_fixpoint(self):
        for prefix in ('PATHS = B\nB = C\nC = tuple((ROOT / ".github/workflows").glob("*.yml"))\n',
                       'C = tuple((ROOT / ".github/workflows").glob("*.yml"))\nB = C\nPATHS = B\n'):
            source = self.invariant('PATHS', prefix)
            self.assertEqual(self.screen(source), [])
            local = 'def test_all():\n' + '\n'.join('    ' + s for s in source.splitlines())
            self.assertEqual(self.screen(local), [])
            self.assert_listed(source.replace('tuple((ROOT / ".github/workflows").glob("*.yml"))',
                                              '[ROOT / ".github/workflows/ci.yml"]'))
        for prefix in ('PATHS = B\nB = PATHS\n', 'PATHS = unknown()\n'):
            self.assert_listed(self.invariant('PATHS', prefix))
        self.assert_listed('PATHS = (ROOT / ".github/workflows").glob("*.yml")\n'
                           'def test_all(PATHS):\n' + '\n'.join('    ' + s for s in self.invariant('PATHS').splitlines()))
        self.assert_listed('PATHS = (ROOT / ".github/workflows").glob("*.yml")\n'
                           'def test_all():\n    PATHS = [ROOT / ".github/workflows/ci.yml"]\n'
                           + '\n'.join('    ' + s for s in self.invariant('PATHS').splitlines()))

    def test_pattern_bindings_and_filename_alias_filters(self):
        source = self.invariant('(ROOT / ".github/workflows").glob(PATTERN)', 'PATTERN = "*.yml"\n')
        self.assertEqual(self.screen(source), [])
        self.assert_listed(source.replace('"*.yml"', '"ci*.yml"'))
        source = self.invariant('(ROOT / ".github/workflows").glob("*.yml")',
                                'ONLY = "ci.yml"\n', '    if candidate.name != ONLY:\n        continue\n')
        self.assert_listed(source)

    def test_accumulation_preserves_element_provenance(self):
        for update in ('bucket.append(parsed["steps"])', 'bucket.extend([parsed["steps"]])',
                       'bucket += [parsed["steps"]]'):
            source = f'''bucket = []
for candidate in (ROOT / ".github/workflows").glob("*.yml"):
    parsed = yaml.safe_load(candidate.read_text())
    {update}
for entries in bucket:
    for entry in entries:
        assert entry["with"]["persist-credentials"] is False
'''
            with self.subTest(update=update):
                self.assertEqual(self.screen(source), [])
                self.assert_listed(source.replace('parsed["steps"]', 'fixed_steps'))
                self.assert_listed(source.replace('.glob("*.yml")', '.glob("ci*.yml")'))
                self.assert_listed(source.replace('for entries in bucket:', 'bucket = [fixed_steps]\nfor entries in bucket:'))

    def test_extend_and_augassign_consume_iterables(self):
        for update in ('bucket.extend(parsed["steps"])', 'bucket += parsed["steps"]',
                       'bucket += (entry,)', 'bucket.extend((entry,))'):
            source = f'''bucket = []
for candidate in (ROOT / ".github/workflows").glob("*.yml"):
    parsed = yaml.safe_load(candidate.read_text())
    for entry in parsed["steps"]:
        {update}
for entry in bucket:
    assert entry["with"]["persist-credentials"] is False
'''
            with self.subTest(update=update):
                self.assertEqual(self.screen(source), [])
                self.assert_listed(source.replace(update, update.replace('parsed["steps"]', 'fixed_steps').replace('(entry,)', '(fixed_step,)')))

    def test_unknown_collection_writes_invalidate_provenance(self):
        source = '''bucket = [entry for candidate in (ROOT / ".github/workflows").glob("*.yml")
          for entry in yaml.safe_load(candidate.read_text())["steps"]]
WRITE
for entry in bucket:
    assert entry["with"]["persist-credentials"] is False
'''
        self.assertEqual(self.screen(source.replace('WRITE', '')), [])
        for write in ('bucket[0] = fixed_step', 'bucket.insert(0, fixed_step)',
                      'bucket *= 0', 'bucket.append(fixed_step)', 'bucket.extend(fixed_steps)'):
            with self.subTest(write=write):
                self.assert_listed(source.replace('WRITE', write))

    def test_tuple_unpacking_does_not_taint_unrelated_slots(self):
        source = '''bucket = []
for candidate in (ROOT / ".github/workflows").glob("*.yml"):
    parsed = yaml.safe_load(candidate.read_text())
    bucket.append((candidate, parsed["steps"]))
for location, entries in bucket:
    for entry in entries:
        assert entry["with"]["persist-credentials"] is False
'''
        self.assertEqual(self.screen(source), [])
        self.assert_listed(source.replace('parsed["steps"]', 'fixed_steps'))
        self.assert_listed(source.replace('location, entries', 'entries, location').replace(
            '(candidate, parsed["steps"])', '(fixed_steps, parsed["steps"])'))

    def test_comprehensions_preserve_only_proven_elements(self):
        source = '''bucket = [(candidate, yaml.safe_load(candidate.read_text())["steps"])
          for candidate in (ROOT / ".github/workflows").glob("*.yml")]
for location, entries in bucket:
    for entry in entries:
        assert entry["with"]["persist-credentials"] is False
'''
        self.assertEqual(self.screen(source), [])
        self.assert_listed(source.replace('yaml.safe_load(candidate.read_text())["steps"]', 'fixed_steps'))
        self.assert_listed(source.replace('.glob("*.yml")', '.glob("ci*.yml")'))
        self.assert_listed(source.replace('.glob("*.yml")]', '.glob("*.yml") if candidate.name == "ci.yml"]'))

    def test_file_filters_and_unrelated_reads_stay_listed(self):
        for body in ('    if candidate.name != "ci.yml":\n        continue\n',
                     '    if candidate.name == "ci.yml":\n        pass\n',
                     '    if candidate.name.startswith("ci.yml"):\n        continue\n',
                     '    if candidate.stem != "ci":\n        continue\n',
                     '    if choose(candidate):\n        continue\n'):
            self.assert_listed(self.invariant('(ROOT / ".github/workflows").glob("*.yml")', body=body))
        source = self.invariant('(ROOT / ".github/workflows").glob("*.yml")')
        for old, new in (('candidate.read_text()', 'FIXED.read_text()'),
                         ('parsed["jobs"]', 'unrelated["jobs"]'),
                         ('job.get("steps", [])', 'fixed_steps'),
                         ('entry["with"]', 'fixed_entry["with"]')):
            with self.subTest(old=old):
                self.assert_listed(source.replace(old, new))

    def test_local_input_aliases_keep_the_checked_field(self):
        source = '''for candidate in (ROOT / ".github/workflows").glob("*.yml"):
    for entry in yaml.safe_load(candidate.read_text())["steps"]:
        inputs = entry.get("with", {})
        actual = inputs.get("persist-credentials")
        assert actual is False
'''
        self.assertEqual(self.screen(source), [])
        # _bind follows the input alias even when the value comes from a fixed file.
        self.assert_listed(source.replace('candidate.read_text()', 'FIXED.read_text()'))
        self.assert_listed(source.replace('inputs.get("persist-credentials")', 'inputs.get("fetch-depth")'))

    def test_parser_and_reader_provenance_stays_attached_to_the_argument(self):
        for reader in ('candidate.read_text()', 'candidate.read_bytes()'):
            for parser in ('yaml.load', 'yaml.safe_load', 'module.workflow_steps', 'module.workflow_jobs'):
                source = self.invariant('(ROOT / ".github/workflows").glob("*.yml")').replace(
                    'yaml.safe_load(candidate.read_text())', f'{parser}({reader})')
                with self.subTest(reader=reader, parser=parser):
                    self.assertEqual(self.screen(source), [])
                    self.assert_listed(source.replace(reader, 'FIXED.read_text()'))
        self.assert_listed(self.invariant('(ROOT / ".github/workflows").glob("*.yml")').replace(
            'yaml.safe_load(candidate.read_text())', 'opaque(candidate.read_text())'))

    def test_class_locals_do_not_become_method_globals(self):
        source = '''PATHS = [ROOT / ".github/workflows/ci.yml"]
class TestAll:
    PATHS = (ROOT / ".github/workflows").glob("*.yml")
    def test_all(self):
'''
        source += '\n'.join('        ' + s for s in self.invariant('PATHS').splitlines())
        self.assert_listed(source)
        self.assertEqual(self.screen(source.replace(
            'PATHS = [ROOT / ".github/workflows/ci.yml"]',
            'PATHS = (ROOT / ".github/workflows").glob("*.yml")').replace(
                '    PATHS = (ROOT / ".github/workflows").glob("*.yml")',
                '    PATHS = [ROOT / ".github/workflows/ci.yml"]')), [])

    def test_conditional_accumulation_and_empty_extensions(self):
        source = '''bucket = []
for candidate in (ROOT / ".github/workflows").glob("*.yml"):
    for entry in yaml.safe_load(candidate.read_text())["steps"]:
        if active:
            bucket.append(entry)
bucket += []
for entry in bucket:
    assert entry["with"]["persist-credentials"] is False
'''
        self.assertEqual(self.screen(source), [])
        self.assert_listed(source.replace('bucket.append(entry)', 'bucket.append(fixed_entry)'))
        self.assert_listed(source.replace('.glob("*.yml")', '.glob("ci*.yml")'))

    def test_conditional_input_aliases_keep_only_common_fields(self):
        source = '''for candidate in (ROOT / ".github/workflows").glob("*.yml"):
    for entry in yaml.safe_load(candidate.read_text())["steps"]:
        if active:
            actual = entry["with"]["persist-credentials"]
        else:
            actual = entry["with"]["fetch-depth"]
        assert actual is False
'''
        self.assert_listed(source)
        self.assertEqual(self.screen(source.replace('"fetch-depth"', '"persist-credentials"')), [])

    def test_tuple_arity_and_async_comprehensions_are_unproven(self):
        source = '''bucket = [(candidate, yaml.safe_load(candidate.read_text())["steps"], extra)
          for candidate in (ROOT / ".github/workflows").glob("*.yml")]
for location, entries in bucket:
    for entry in entries:
        assert entry["with"]["persist-credentials"] is False
'''
        self.assert_listed(source)
        self.assertEqual(self.screen(source.replace(', extra)', ')')), [])
        self.assert_listed(source.replace(', extra)', ')').replace('for candidate in', 'async for candidate in'))
        mixed = '''bucket = []
for candidate in (ROOT / ".github/workflows").glob("*.yml"):
    entries = yaml.safe_load(candidate.read_text())["steps"]
    bucket.append((candidate, entries))
    bucket.append((candidate, entries, extra))
for location, entries in bucket:
    for entry in entries:
        assert entry["with"]["persist-credentials"] is False
'''
        self.assert_listed(mixed)

    def test_nested_scope_and_late_local_binding_do_not_borrow_globals(self):
        source = 'PATHS = (ROOT / ".github/workflows").glob("*.yml")\ndef test_all():\n'
        source += '\n'.join('    ' + s for s in self.invariant('PATHS').splitlines())
        self.assertEqual(self.screen(source + '\n    def other(PATHS):\n        return PATHS'), [])
        self.assert_listed(source + '\n    if flag:\n        PATHS = []')

    def test_unrelated_filename_comparisons_do_not_filter_the_enumeration(self):
        self.assertEqual(self.screen(self.invariant('(ROOT / ".github/workflows").glob("*.yml")',
                                                    body='    if outside.name == "ci.yml":\n        pass\n')), [])
        # A content-based class filter still visits every path; the reader must
        # consume the path before the filter is classified.
        self.assertEqual(self.screen(self.invariant('(ROOT / ".github/workflows").glob("*.yml")',
                                                    body='    if "workflow_run" not in yaml.safe_load(candidate.read_text()).get("on", {}):\n        continue\n')), [])

    def test_readers_and_workflow_helper_consume_paths_before_content_filters(self):
        for expression, needle in (('candidate.read_text()', '"workflow_run"'),
                                   ('candidate.read_bytes()', 'b"workflow_run"'),
                                   ('_workflow(candidate.name).get("on", {})', '"workflow_run"')):
            source = self.invariant('(ROOT / ".github/workflows").glob("*.yml")',
                                    body=f'    if {needle} not in {expression}:\n        continue\n')
            self.assertEqual(self.screen(source), [], expression)
            self.assert_listed(source.replace(expression, 'candidate.name'))

    def test_raw_paths_do_not_become_parsed_members_by_accumulation(self):
        source = '''bucket = []
for candidate in (ROOT / ".github/workflows").glob("*.yml"):
    bucket.append(candidate)
    bucket.extend(yaml.safe_load(candidate.read_text())["steps"])
for entry in bucket:
    assert entry["with"]["persist-credentials"] is False
'''
        self.assert_listed(source)
        self.assert_listed(source.replace('    bucket.extend(yaml.safe_load(candidate.read_text())["steps"])\n', ''))
        self.assertEqual(self.screen(source.replace('    bucket.append(candidate)\n', '')), [])

    def test_pr_cli_uses_head_context_for_an_existing_binding(self):
        for source in (self.PR_GATE, self.AI_RESEARCHER):
            reader = 'text = (ROOT / ".github/workflows/ci.yml").read_text()\n'
            head = reader + source
            # ai-researcher's real hunk adds the class, leaving WORKFLOWS outside it.
            offset = 1 if source == self.AI_RESEARCHER else 0
            lines = source.splitlines()[offset:]
            patch = '+++ b/tests/test_live.py\n@@ -0,0 +' + str(2 + offset) + ',99 @@\n'
            patch += '\n'.join('+' + s for s in lines) + '\n'
            for contents, want in ((head, 0), (head.replace('.glob("*.yml")', '.glob("ci*.yml")').replace(
                    ".glob('*.yml')", ".glob('ci*.yml')").replace(
                    "['git', 'ls-files', '--', 'action.yml', '*/action.yml']",
                    "['git', 'ls-files', '--', 'local/action.yml']"), 1)):
                # The hunk must also show the narrowed source for the pr-gate fixture.
                actual_patch = patch
                if want and not offset:
                    actual_patch = patch.replace(".glob('*.yml')", ".glob('ci*.yml')").replace(
                        "['git', 'ls-files', '--', 'action.yml', '*/action.yml']",
                        "['git', 'ls-files', '--', 'local/action.yml']")
                code, out, _, _ = Main().run_main(actual_patch, {}, {'tests/test_live.py': contents})
                self.assertEqual(code, want, out)
                self.assertEqual(len(out), want)

    def test_git_listings_do_not_accept_another_command_or_untracked_files(self):
        for command in ('["echo", "ls-files", "action.yml"]',
                        '["git", "ls-files", "--others", "action.yml"]',
                        '["git", "ls-files", "--", "*/action.yml"]'):
            self.assert_listed(self.invariant(f'subprocess.run({command}).stdout.splitlines()'))
        self.assertEqual(self.screen(self.invariant(
            'subprocess.run(("git", "ls-files", "action.yml")).stdout.splitlines()')), [])

    def test_generators_and_event_replace_preserve_provenance(self):
        source = '''bucket = ((candidate, yaml.safe_load(candidate.read_text())["steps"])
          for candidate in (ROOT / ".github/workflows").glob("*.yml"))
for location, entries in bucket:
    for entry in entries:
        assert entry["with"]["run-id"].replace(" ", "") == "${{github.event.workflow_run.id}}"
'''
        self.assertEqual(self.screen(source), [])
        self.assert_listed(source.replace('.glob("*.yml")', '.glob("ci*.yml")'))

    def test_nested_local_assignments_stay_in_their_scope(self):
        source = 'PATHS = (ROOT / ".github/workflows").glob("*.yml")\ndef test_all():\n'
        source += '\n'.join('    ' + s for s in self.invariant('PATHS').splitlines())
        self.assertEqual(self.screen(source + '\n    def other():\n        PATHS = []'), [])

    def test_non_additive_accumulation_and_fixed_path_transforms_stay_listed(self):
        source = '''bucket = []
for candidate in (ROOT / ".github/workflows").glob("*.yml"):
    for entry in yaml.safe_load(candidate.read_text())["steps"]:
        bucket *= entry.get("repeat", 1)
for entry in bucket:
    assert entry["with"]["persist-credentials"] is False
'''
        self.assert_listed(source)
        self.assert_listed(self.invariant('(ROOT / ".github/workflows").glob("*.yml")').replace(
            'candidate.read_text()', '(candidate.parent / "ci.yml").read_text()'))

    def test_unresolved_and_member_relative_roots_do_not_become_sources(self):
        self.assert_listed(self.invariant('opaque().glob(".github/workflows/*.yml")'))
        source = 'for original in (ROOT / ".github/workflows").glob("*.yml"):\n'
        source += '\n'.join('    ' + s for s in self.invariant(
            '(original.parent / ".github/workflows").glob("*.yml")').splitlines())
        self.assert_listed(source)

    def test_whitespace_normalised_event_expression_requires_provenance(self):
        for check in ('assert entry["with"]["run-id"] == "${{github.event.workflow_run.id}}"',
                      'self.assertEqual("".join(entry["with"]["run-id"].split()), "${{github.event.workflow_run.id}}")'):
            source = self.invariant('(ROOT / ".github/workflows").glob("*.yml")').replace(
                'assert entry["with"]["persist-credentials"] is False', check)
            self.assertEqual(self.screen(source), [])
            self.assert_listed(check)
            self.assert_listed(source.replace('.glob("*.yml")', '.glob("ci*.yml")'))
            self.assert_listed(source.replace('github.event.workflow_run.id', 'secrets.RUN_ID'))

    DOCS_HUB = '''def test_every_checkout_disables_persisted_credentials():
    checkouts = [
        (path.name, job_name, step)
        for path in sorted((_REPO / ".github/workflows").glob("*"))
        for job_name, job in _workflow(path.name)["jobs"].items()
        for step in job.get("steps", [])
        if step.get("uses", "").partition("@")[0] == "actions/checkout"
    ]
    assert checkouts, "No actions/checkout steps found"
    for filename, job_name, step in checkouts:
        assert step.get("with", {}).get("persist-credentials") is False, (filename, job_name)
'''

    def test_star_enumerates_docs_hub_workflows(self):
        self.assertEqual(self.screen(self.DOCS_HUB), [])
        self.assertEqual(self.screen(self.DOCS_HUB.replace(
            '(_REPO / ".github/workflows").glob("*")', '_REPO.glob(".github/workflows/*")')), [])

    def star_near_miss(self, pattern):
        self.assert_listed(self.DOCS_HUB.replace('.glob("*")', f'.glob("{pattern}")'))
        self.assert_listed(self.DOCS_HUB.replace('(_REPO / ".github/workflows").glob("*")',
                                                 f'_REPO.glob(".github/workflows/{pattern}")'))

    def test_star_rejects_ci_prefix_A02(self):
        self.star_near_miss('ci*')

    def test_star_rejects_ci_substring_A03(self):
        self.star_near_miss('*ci*')

    def test_star_rejects_c_prefix_A04(self):
        self.star_near_miss('c*')

    def test_star_rejects_github_directory_A05(self):
        self.assert_listed(self.DOCS_HUB.replace('".github/workflows"', '".github"'))
        self.assert_listed(self.DOCS_HUB.replace('(_REPO / ".github/workflows").glob("*")',
                                                 '_REPO.glob(".github/*")'))

    def test_star_rejects_json_A06(self):
        self.star_near_miss('*.json')

    def assigned_filter(self, assignment, condition):
        source = self.invariant('(ROOT / ".github/workflows").glob("*.yml")')
        marker = '    parsed = yaml.safe_load(candidate.read_text())\n'
        insert = ''.join('    ' + line + '\n' for line in assignment.splitlines())
        return source.replace(marker, marker + insert + f'    if {condition}:\n        continue\n')

    def test_path_dependency_survives_opaque_assignments(self):
        for value in ('str(candidate.name)', 'os.path.basename(candidate)',
                      'candidate.name.lower()', 'f"{candidate.name}"',
                      'candidate.name == "ci.yml"', 'candidate.name not in {"ci.yml"}'):
            with self.subTest(value=value):
                source = self.assigned_filter('selected = ' + value, 'selected != "ci.yml"')
                self.assert_listed(source)
                content = source.replace('candidate.name', 'parsed.get("name", "")').replace(
                    'os.path.basename(candidate)', 'os.path.basename(parsed.get("name", ""))')
                self.assertEqual(self.screen(content), [])

    def test_path_dependency_reaches_assignment_fixpoint(self):
        source = self.assigned_filter('last = middle\nmiddle = first\nfirst = str(candidate.name)',
                                      'last != "ci.yml"')
        self.assert_listed(source)
        self.assertEqual(self.screen(source.replace('candidate.name', 'parsed.get("name", "")')), [])

    def test_path_dependency_augmented_assignment(self):
        source = self.assigned_filter('selected = ""\nselected += str(candidate.name)', 'selected != "ci.yml"')
        self.assert_listed(source)
        self.assertEqual(self.screen(source.replace('candidate.name', 'parsed.get("name", "")')), [])

    def test_path_dependency_annotated_assignment(self):
        source = self.assigned_filter('selected: str = str(candidate.name)', 'selected != "ci.yml"')
        self.assert_listed(source)
        self.assertEqual(self.screen(source.replace('candidate.name', 'parsed.get("name", "")')), [])
        self.assertEqual(self.screen(self.assigned_filter('selected = parsed.get("name")\nselected: str',
                                                          'selected != "CI"')), [])

    def test_path_dependency_walrus_assignment(self):
        for assignment in ('unused = (selected := str(candidate.name))', '(selected := str(candidate.name))'):
            source = self.assigned_filter(assignment, 'selected != "ci.yml"')
            self.assert_listed(source)
            self.assertEqual(self.screen(source.replace('candidate.name', 'parsed.get("name", "")')), [])

    def test_path_dependency_tuple_assignment(self):
        source = self.assigned_filter('selected, unused = str(candidate.name).split(".")', 'selected != "ci"')
        self.assert_listed(source)
        self.assertEqual(self.screen(source.replace('candidate.name', 'parsed.get("name", "")')), [])
        self.assertEqual(self.screen(self.assigned_filter('metadata = {"mode": "CI"}\nmetadata["path"] = str(candidate.name)',
                                                          'metadata["mode"] != "CI"')), [])

    def test_path_dependency_comprehension_target(self):
        for expression in ('[str(name) for name in (candidate.name,)]',
                           '(str(name) for name in (candidate.name,))',
                           '{str(name) for name in (candidate.name,)}',
                           '{str(name): 1 for name in (candidate.name,)}',
                           '{"key": str(name) for name in (candidate.name,)}'):
            with self.subTest(expression=expression):
                source = self.assigned_filter('names = ' + expression + '\nselected = next(iter(names))',
                                              'selected != "ci.yml"')
                self.assert_listed(source)
                self.assertEqual(self.screen(source.replace('candidate.name', 'parsed.get("name", "")')), [])

    def test_path_dependency_opaque_comprehension_iterable(self):
        source = self.assigned_filter('names = [str(name) for name in opaque(candidate)]\nselected = next(iter(names))',
                                      'selected != "ci.yml"')
        self.assert_listed(source)
        self.assertEqual(self.screen(source.replace('opaque(candidate)', 'opaque(parsed)')), [])

    def test_path_dependency_walrus_in_comprehension_keeps_scope(self):
        source = self.assigned_filter('names = [(selected := str(name)) for name in (candidate.name,)]',
                                      'selected != "ci.yml"')
        self.assert_listed(source)
        self.assertEqual(self.screen(source.replace('candidate.name', 'parsed.get("name", "")')), [])
        # Comprehension iteration targets do not replace an enclosing content alias.
        source = self.assigned_filter('name = parsed.get("name")\nnames = [str(name) for name in (candidate.name,)]',
                                      'name != "CI"')
        self.assertEqual(self.screen(source), [])
        source = self.assigned_filter('unused = [1 for name in (candidate.name,) if name != "other" '
                                      'if (selected := str(name)) != "skip"]', 'selected != "ci.yml"')
        self.assert_listed(source)
        self.assertEqual(self.screen(source.replace('candidate.name', 'parsed.get("name", "")')), [])

    def test_path_dependency_in_comprehension_filter(self):
        source = self.assigned_filter('selected = [1 for name in (candidate.name,) if str(name) != "ci.yml"]',
                                      'selected')
        self.assert_listed(source)
        self.assertEqual(self.screen(source.replace('candidate.name', 'parsed.get("name", "")')), [])

    def control_case(self, control='', tail=''):
        source = self.invariant('(ROOT / ".github/workflows").glob("*.yml")')
        marker = '    parsed = yaml.safe_load(candidate.read_text())\n'
        source = source.replace(marker, marker + ''.join('    ' + line + '\n' for line in control.splitlines()))
        source += ''.join('    ' + line + '\n' for line in tail.splitlines())
        return 'def test_all():\n' + '\n'.join('    ' + line for line in source.splitlines())

    def test_unconditional_break_narrows_enumeration(self):
        self.assert_listed(self.control_case(tail='break'))
        self.assertEqual(self.screen(self.control_case(tail='if parsed.get("stop"):\n    break')), [])

    def test_nested_unconditional_break_narrows_enumeration(self):
        source = self.control_case().replace('                assert entry', '                break\n                assert entry')
        self.assert_listed(source)
        self.assertEqual(self.screen(source.replace('                break',
                                                    '                if entry.get("stop"):\n                    break')), [])

    def test_path_while_narrows_enumeration(self):
        self.assert_listed(self.control_case('while candidate.name != "ci.yml":\n    break'))
        self.assert_listed(self.control_case('while candidate.name != "ci.yml":\n    candidate = CI_FILE'))
        self.assertEqual(self.screen(self.control_case('while parsed.get("retry"):\n    break')), [])

    def test_path_match_narrows_enumeration(self):
        control = 'match candidate.name:\n    case "ci.yml":\n        pass\n    case _:\n        continue'
        self.assert_listed(self.control_case(control))
        self.assertEqual(self.screen(self.control_case(control.replace('candidate.name', 'parsed.get("name")'))), [])

    def test_path_match_guard_narrows_enumeration(self):
        control = 'match parsed.get("name"):\n    case _ if candidate.name != "ci.yml":\n        continue'
        self.assert_listed(self.control_case(control))
        self.assertEqual(self.screen(self.control_case(control.replace('candidate.name', 'parsed.get("name")'))), [])

    def test_path_try_handler_narrows_enumeration(self):
        control = 'try:\n    WANTED.index(candidate.name)\nexcept ValueError:\n    continue'
        self.assert_listed(self.control_case(control))
        self.assertEqual(self.screen(self.control_case(control.replace('candidate.name', 'parsed.get("name")'))), [])
        self.assert_listed(self.control_case(control.replace('continue', 'break')))
        self.assertEqual(self.screen(self.control_case(control.replace('continue', 'pass'))), [])

    def test_path_read_in_try_handler_stays_narrowing(self):
        control = 'try:\n    yaml.safe_load(candidate.read_text())\nexcept ValueError:\n    continue'
        self.assert_listed(self.control_case(control))
        self.assertEqual(self.screen(self.control_case(control.replace('candidate.read_text()', '"fixture"'))), [])

    def test_unmodelled_raise_return_and_yield_narrow_enumeration(self):
        for transfer in ('raise ValueError()', 'return', 'yield parsed', 'yield from parsed'):
            with self.subTest(transfer=transfer):
                self.assert_listed(self.control_case(tail=transfer))
                nested = 'def unrelated():\n    ' + transfer
                self.assertEqual(self.screen(self.control_case(tail=nested)), [])
        self.assertEqual(self.screen(self.control_case(tail='deferred = lambda: (yield parsed)')), [])

    def test_unmodelled_exits_in_conditional_and_try_branches(self):
        for control in ('if parsed.get("stop"):\n    return',
                        'if parsed.get("stop"):\n    pass\nelse:\n    return',
                        'while parsed.get("retry"):\n    return',
                        'match parsed.get("name"):\n    case _:\n        return',
                        'try:\n    return\nexcept ValueError:\n    pass',
                        'try:\n    inspect(parsed)\nexcept ValueError:\n    return',
                        'try:\n    inspect(parsed)\nfinally:\n    yield parsed',
                        'try:\n    inspect(parsed)\nexcept ValueError:\n    pass\nelse:\n    return'):
            with self.subTest(control=control):
                self.assert_listed(self.control_case(control))
                safe = control.replace('return', 'pass').replace('yield parsed', 'pass')
                self.assertEqual(self.screen(self.control_case(safe)), [])

    def test_executed_class_body_is_not_a_function_definition(self):
        self.assert_listed(self.control_case(tail='class Stop:\n    raise ValueError()'))
        self.assertEqual(self.screen(self.control_case(tail='class Helper:\n    def stop(self):\n        raise ValueError()')), [])
        control = 'try:\n    class Body:\n        WANTED.index(candidate.name)\nexcept ValueError:\n    continue'
        self.assert_listed(self.control_case(control))
        self.assertEqual(self.screen(self.control_case(control.replace('candidate.name', 'parsed.get("name")'))), [])
        control = 'class Body:\n    selected = str(candidate.name)\n    while selected != "ci.yml":\n        break'
        self.assert_listed(self.control_case(control))
        self.assertEqual(self.screen(self.control_case(control.replace('candidate.name', 'parsed.get("name")'))), [])

    def test_content_alias_does_not_gain_path_dependency(self):
        source = self.control_case().replace('                assert entry',
                                             '                action, pin = entry.get("uses", "").split("@")\n'
                                             '                if action != "actions/checkout":\n                    continue\n'
                                             '                assert entry')
        self.assertEqual(self.screen(source), [])
        self.assertEqual(self.screen(self.PR_GATE), [])
        self.assertEqual(self.screen(self.AI_RESEARCHER), [])

    def test_narrowing_does_not_exempt_an_outer_member(self):
        source = '''for candidate in (ROOT / ".github/workflows").glob("*.yml"):
    parsed = yaml.safe_load(candidate.read_text())
    for job in parsed["jobs"].values():
        for entry in job["steps"]:
            for other in (ROOT / ".github/workflows").glob("*.yml"):
                if other.name != "ci.yml":
                    continue
                assert entry["with"]["persist-credentials"] is False
'''
        self.assert_listed(source)
        self.assertEqual(self.screen(source.replace('other.name', 'parsed.get("name")')), [])

    def test_narrowing_invalidates_new_accumulation_of_outer_members(self):
        source = '''entries = []
for candidate in (ROOT / ".github/workflows").glob("*.yml"):
    parsed = yaml.safe_load(candidate.read_text())
    for other in (ROOT / ".github/workflows").glob("*.yml"):
        if other.name != "ci.yml":
            continue
        entries.extend(parsed["jobs"]["build"]["steps"])
for entry in entries:
    assert entry["with"]["persist-credentials"] is False
'''
        self.assert_listed(source)
        self.assertEqual(self.screen(source.replace('other.name', 'parsed.get("name")')), [])

    def test_narrowing_preserves_unchanged_collections(self):
        source = '''entries = [entry for path in (ROOT / ".github/workflows").glob("*.yml")
           for job in yaml.safe_load(path.read_text())["jobs"].values() for entry in job["steps"]]
for candidate in (ROOT / ".github/workflows").glob("*.yml"):
    if candidate.name != "ci.yml":
        continue
for entry in entries:
    assert entry["with"]["persist-credentials"] is False
'''
        self.assertEqual(self.screen(source), [])
        self.assert_listed(source.replace('entries = [entry', 'entries = [fixed_entry'))


class ExecutedAndDerivedReads(unittest.TestCase):
    """What a test reads back from an executed step's output, or builds from the workflows, is no pin."""

    STAGE = 'staged = tmp_path / _step("Upload the coverage raise artifact")["with"]["path"]\n'
    GREP = '''f"grep -qx '{upload['with']['name']}'"'''
    ARTIFACTS = '''_step("List the triggering run's artifacts")["run"]'''

    def screen(self, source):
        return mod.findings(diff('tests/test_wf.py', *source.splitlines()), (), lambda p: True)

    def test_files_an_executed_step_wrote_under_a_path_joined_from_an_input_are_not_flagged(self):
        for source in (
            self.STAGE + 'assert sorted(path.name for path in staged.iterdir()) == [\n    "base.txt", "measured.txt"]',
            self.STAGE + 'assert (staged / "measured.txt").read_text() == f"{expected}\\n"',
            self.STAGE + 'assert (staged / "base.txt").read_text() == "abc\\n"',
            # the join goes through an alias of the input
            'where = step["with"]["path"]\nstaged = tmp_path / where\nassert (staged / "base.txt").read_text() == "abc\\n"',
        ):
            with self.subTest(source=source):
                self.assertEqual(self.screen(source), [])

    def test_the_input_itself_and_an_unjoined_alias_are_still_flagged(self):
        for source in (
            self.STAGE + 'declared = _step("Upload the coverage raise artifact")["with"]["path"]\nassert declared == "coverage-raise"',
            self.STAGE + 'assert _step("Upload the coverage raise artifact")["with"]["path"] == "coverage-raise"',
            'where = step["with"]["path"]\nstaged = tmp_path / where\nassert where == "coverage-raise"',
            'where = step["with"]["path"]\nstaged = tmp_path / where\nassert sorted(where.split("/")) == ["a", "b"]',
        ):
            with self.subTest(source=source):
                self.assertEqual(len(self.screen(source)), 1)

    def test_a_string_composed_around_an_input_against_another_steps_read_value_is_not_flagged(self):
        for source in (f'assert {self.GREP} in {self.ARTIFACTS}',
                       f'assert {self.ARTIFACTS} == {self.GREP}',
                       f'assert {self.GREP} in _step(\n    "List the triggering run\'s artifacts")["run"]'):
            with self.subTest(source=source):
                self.assertEqual(self.screen(source), [])

    def test_a_string_composed_around_an_input_against_a_held_value_is_still_flagged(self):
        for source in (f'''assert {self.GREP} in "grep -qx 'coverage-raise'"''',
                       f'assert {self.GREP} in expected["run"]',
                       f'''assert {self.GREP} == "grep -qx 'coverage-raise'"'''):
            with self.subTest(source=source):
                self.assertEqual(len(self.screen(source)), 1)

    READ = 'reference = dockerfile_from_reference(ROOT / ".github/actions/postgres-image/Dockerfile")\n'
    MATCH = 'match = re.search(r"/postgres:(\\d+)(?:\\.|@)", reference)\n'
    COMPARE = 'for _, _, step in _client_steps():\n    package = step.get("with", {}).get("packages", "")\n'

    def test_a_value_derived_from_a_pin_source_compared_to_an_input_is_not_flagged(self):
        for source in (
            self.READ + self.MATCH + self.COMPARE + '    assert package == f"postgresql-client-{match[1]}"',
            # the read sits inside the comparison
            self.COMPARE + '    assert package == f"postgresql-client-{re.search(r"postgres:(\\d+)", read(ROOT / "Dockerfile"))[1]}"',
        ):
            with self.subTest(source=source):
                self.assertEqual(self.screen(source), [])

    def test_a_value_not_derived_from_a_pin_source_compared_to_an_input_is_still_flagged(self):
        for source in (
            # the major is a held literal
            'major = "16"\n' + self.COMPARE + '    assert package == f"postgresql-client-{major}"',
            # the match is over text that is no pin source
            'reference = fetch(ROOT / "notes/readme.txt")\n' + self.MATCH + self.COMPARE
            + '    assert package == f"postgresql-client-{match[1]}"',
            # a literal beside the derived value
            self.READ + self.MATCH + self.COMPARE + '    assert package == "postgresql-client-16"',
            self.COMPARE + '    assert package == f"postgresql-client-{16}"',
        ):
            with self.subTest(source=source):
                self.assertEqual(len(self.screen(source)), 1)

    STEPS = ('steps = _load(SIZE_BASELINE)["jobs"]["size-baseline"]["steps"]\n'
             'stage = next(s for s in steps if s.get("id") == "stage")\n'
             'upload = next(s for s in steps\n'
             '              if s.get("uses", "").startswith("actions/upload-artifact@"))\n')
    FINDALL = 're.findall(r\'data="\\$\\{RUNNER_TEMP\\}/([a-z0-9-]+)"\', '
    NAME = 'assert upload["with"]["name"] == '

    def test_an_input_against_a_value_a_call_derives_from_another_steps_field_is_not_flagged(self):
        for source in (
            # seen in an earlier screening
            self.STEPS + 'directories = ' + self.FINDALL + '\n                         stage["run"])\n'
            + self.NAME + 'directories[0]',
            # the call sits inside the comparison
            self.STEPS + self.NAME + self.FINDALL + 'stage["run"])[0]',
            # the other step is selected by index
            self.STEPS.replace('next(s for s in steps if s.get("id") == "stage")', 'steps[1]')
            + self.NAME + self.FINDALL + 'stage["run"])[0]',
            # the other step is read through a name the screen already reads from the workflows
            'doc = yaml.safe_load((ROOT / ".github/workflows/size.yml").read_text())\n'
            'stage = doc["jobs"]["size"]["steps"][0]\n' + self.STEPS.split('\n', 2)[2]
            + self.NAME + self.FINDALL + 'stage["run"])[0]',
            'doc = yaml.safe_load((ROOT / ".github/workflows/size.yml").read_text())\n'
            'stage = doc["jobs"]["size"]["steps"][0]\n' + self.STEPS.split('\n', 2)[2]
            + 'directories = ' + self.FINDALL + 'stage["run"])\n' + self.NAME + 'directories[0]',
        ):
            with self.subTest(source=source):
                self.assertEqual(self.screen(source), [])

    def test_an_input_against_a_value_not_derived_from_another_steps_field_is_still_flagged(self):
        for source in (
            # a held literal
            self.STEPS + self.NAME + '"size-baseline"',
            # the call is handed a literal, or a name bound to one, not a step's field
            self.STEPS + 'directories = ' + self.FINDALL + '\'data="${RUNNER_TEMP}/size-baseline"\')\n'
            + self.NAME + 'directories[0]',
            self.STEPS + 'RUN = \'data="${RUNNER_TEMP}/size-baseline"\'\ndirectories = ' + self.FINDALL + 'RUN)\n'
            + self.NAME + 'directories[0]',
            # the field is the same step's whose input is read
            self.STEPS + 'directories = ' + self.FINDALL + 'upload["run"])\n' + self.NAME + 'directories[0]',
            self.STEPS + self.NAME + self.FINDALL + 'upload["run"])[0]',
            # the value only passes through a call with no step-field argument
            self.STEPS + self.NAME + '"size-baseline-x".split("-x")[0]',
            # the "step" is a held dict, or parsed from literal fixture text, not read from the workflows
            self.STEPS + 'expected = {"run": \'data="${RUNNER_TEMP}/size-baseline"\'}\n'
            + self.NAME + self.FINDALL + 'expected["run"])[0]',
            self.STEPS + self.NAME + self.FINDALL + 'expected["run"])[0]',
            'FIXTURE = "jobs: {}"\n' + self.STEPS.replace('_load(SIZE_BASELINE)', 'yaml.safe_load(FIXTURE)')
            + self.NAME + self.FINDALL + 'stage["run"])[0]',
            self.STEPS.replace('_load(SIZE_BASELINE)', 'yaml.safe_load("jobs: {}")')
            + self.NAME + self.FINDALL + 'stage["run"])[0]',
            'fixture = [{"run": \'data="${RUNNER_TEMP}/size-baseline"\'}]\nstage = fixture[0]\n'
            + self.STEPS.split('\n', 2)[2] + self.NAME + self.FINDALL + 'stage["run"])[0]',
        ):
            with self.subTest(source=source):
                self.assertEqual(len(self.screen(source)), 1)


class OwnSuite(unittest.TestCase):
    """The screen's own test suite, in a repository that ships it, holds fixture text, not pin tests."""

    LOADER = ("KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n"
              "spec = importlib.util.spec_from_file_location('check_pin_tests', os.path.join(KIT, 'check_pin_tests.py'))\n"
              "mod = importlib.util.module_from_spec(spec)\n"
              "spec.loader.exec_module(mod)\n")
    FIXTURES = '''

class Fixtures(unittest.TestCase):
    def test_a_read_input_is_flagged(self):
        text = diff('tests/test_wf.py', 'WF = (ROOT / ".github/workflows/c.yml").read_text()',
                    'assert step["with"]["fetch-depth"] == 2')
        self.assertEqual(len(mod.findings(text)), 1)

    def test_a_version_reference(self):
        self.assertEqual(len(self.flagged(diff('tests/test_wf.py', 'assert step.get("uses") == "actions/checkout@v4"'))), 1)

    def test_a_source_sample(self):
        source = """
text = open(".github/workflows/ci.yml").read()
assert steps[0]["uses"] == "actions/checkout@v4"
assert steps[0]["with"]["fetch-depth"] == 2
"""
        self.assertEqual(self.screen(source), [])
'''
    PATH = 'import/tests/test_check_pin_tests.py'
    READ = "WF = (ROOT / '.github/workflows/ci.yml').read_text()\n"
    ASSERT = "\ndef test_the_real_checkout(steps):\n    assert steps[0]['with']['fetch-depth'] == 2\n"

    def screen(self, suite):
        """Every way a caller couples the file: the diff's own text, and the whole-file workflow reader."""
        text = added(self.PATH, suite)
        return (mod.findings(text, {SHA}), mod.findings(text, {SHA}, lambda path: mod.couples(suite, 'workflow')))

    def assert_exempt(self, suite):
        self.assertEqual(self.screen(suite), ([], []))
        for kind in ('workflow', 'pin-shape'):
            self.assertFalse(mod.couples(suite, kind), kind)

    def assert_listed(self, suite):
        for found in self.screen(suite):
            self.assertTrue(found)

    def test_a_suite_loading_the_screen_by_file_location_is_input_not_a_pin_test(self):
        self.assert_exempt('import importlib.util\nimport os\nimport unittest\n\n' + self.LOADER + self.FIXTURES)

    def test_a_suite_importing_the_screen_is_input_not_a_pin_test(self):
        for loader in ('import check_pin_tests as mod\n', 'from check_pin_tests import findings\n',
                       "spec = importlib.util.spec_from_file_location('check_pin_tests', KIT / 'check_pin_tests.py')\n"):
            with self.subTest(loader=loader):
                self.assert_exempt('import unittest\n' + loader + self.FIXTURES)

    def test_a_suite_only_naming_the_screen_is_still_listed(self):
        # (a) a comment or a string, the import statement's text included, loads nothing
        for mention in ('# exercises check_pin_tests\n', "NAME = 'check_pin_tests'\n",
                        'NOTE = "import check_pin_tests"\n', 'NOTE = """from check_pin_tests import findings"""\n'):
            with self.subTest(mention=mention):
                self.assert_listed(mention + self.READ + self.ASSERT)

    def test_a_suite_loading_a_similarly_named_module_is_still_listed(self):
        # (b) another module, or the screen's name bound to another file
        for loader in ('import check_pin_tests_helper\n', 'import my_check_pin_tests\n',
                       'from check_pin_tests_helper import findings\n', 'from my_check_pin_tests import findings\n',
                       "spec = importlib.util.spec_from_file_location('check_pin_tests', 'my_check_pin_tests.py')\n",
                       "spec = importlib.util.spec_from_file_location('helper', 'check_pin_tests.py')\n"):
            with self.subTest(loader=loader):
                self.assert_listed(loader + self.READ + self.ASSERT)

    def test_a_suite_loading_the_screen_that_also_reads_the_workflows_is_still_listed(self):
        # (c) a path join or a reader call outside string literals names the workflows
        for read in (self.READ, "WF = open('.github/workflows/ci.yml').read()\n",
                     "WF = Path('.github/workflows/ci.yml').read_text()\n",
                     "WF = [p.read_text() for p in (ROOT / '.github' / 'workflows').glob('*.yml')]\n",
                     "WF = open(os.path.join(ROOT, '.github', 'workflows', 'ci.yml')).read()\n"):
            with self.subTest(read=read):
                self.assert_listed('import check_pin_tests as mod\n' + read + self.FIXTURES + self.ASSERT)


if __name__ == '__main__':
    unittest.main()
