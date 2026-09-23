"""Static checks for concrete hidden-state access; no environment introspection."""

import ast
import hashlib
import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
HIDDEN_MEMBERS = {"__closure__", "__globals__", "__code__", "gi_frame", "cr_frame", "f_globals", "f_locals"}
HIDDEN_CALLS = {"gc.get_objects", "gc.get_referrers", "gc.get_referents",
                "inspect.getclosurevars", "inspect.currentframe", "sys._getframe"}
PARTICIPANT_SOURCES = frozenset({
    "agent_template.py", "environment.py", "mock_environment.py",
    "scoring_core.py", "local_eval.py", "make_submission.py",
})


def forbidden_patterns(source):
    """Return locations and categories, never source lines or matching values.

    Ordinary imports of inspect/gc and legitimate calls such as signature/collect
    are allowed. This conservative AST check is not a sandbox or proof against
    deliberately obfuscated access.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        return [(error.lineno or 0, "invalid-python")]
    aliases = {}
    env_names = {"env"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name.split(".")[0]] = alias.name
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{node.module or ''}.{alias.name}"
        elif isinstance(node, ast.FunctionDef) and node.name == "act":
            args = node.args.posonlyargs + node.args.args
            if len(args) >= 2:
                env_names.add(args[1].arg)

    def qualified(node):
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return qualified(node.value) + "." + node.attr
        return ""

    assignments = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)]
    for _ in range(len(assignments)):
        changed = False
        for node in assignments:
            value = qualified(node.value)
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                if isinstance(node.value, ast.Name) and node.value.id in env_names and target.id not in env_names:
                    env_names.add(target.id)
                    changed = True
                if value in HIDDEN_CALLS and aliases.get(target.id) != value:
                    aliases[target.id] = value
                    changed = True
        if not changed:
            break

    findings = []
    for node in ast.walk(tree):
        kind = None
        if isinstance(node, ast.Attribute):
            if node.attr in HIDDEN_MEMBERS:
                kind = "hidden-state-attribute"
            elif isinstance(node.value, ast.Name) and node.value.id in env_names and node.attr.startswith("_"):
                kind = "private-env-attribute"
        elif isinstance(node, ast.Call):
            name = qualified(node.func)
            if name in HIDDEN_CALLS:
                kind = "hidden-state-call"
            elif name in {"getattr", "hasattr"} and len(node.args) >= 2:
                member = node.args[1]
                if isinstance(member, ast.Constant) and isinstance(member.value, str):
                    if member.value in HIDDEN_MEMBERS:
                        kind = "hidden-state-lookup"
                    elif isinstance(node.args[0], ast.Name) and node.args[0].id in env_names and member.value.startswith("_"):
                        kind = "private-env-lookup"
            elif name in {"vars", "inspect.getmembers", "inspect.getmembers_static"} and node.args:
                if isinstance(node.args[0], ast.Name) and node.args[0].id in env_names:
                    kind = "env-state-enumeration"
        if kind:
            findings.append((node.lineno, kind))
    return findings


class SecurityPatternTests(unittest.TestCase):
    def test_participant_sources_match_supplied_package(self):
        manifest = json.loads((ROOT / "scripts" / "participant_package_manifest.json").read_text(encoding="utf-8"))
        checksums = manifest["files"]
        for name in PARTICIPANT_SOURCES:
            with self.subTest(source=name):
                self.assertIn(name, checksums)
                self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), checksums[name])

    def test_concrete_bypass_calls_and_aliases_are_rejected(self):
        cases = (
            "import gc as g\nx = g.get_objects()",
            "from gc import get_objects as collect\nx = collect()",
            "import inspect as i\nx = i.getclosurevars(callback)",
            "from inspect import getclosurevars as probe\nf = probe\nx = f(callback)",
            "x = callback.__closure__", 'x = getattr(callback, "__globals__")',
            "copy = env\nx = copy._effects", 'x = getattr(env, "_effects")',
            "import inspect\nx = inspect.getmembers(env)",
            "x = vars(env)", "import sys\nx = sys._getframe()",
        )
        for index, source in enumerate(cases):
            with self.subTest(case=index):
                self.assertTrue(forbidden_patterns(source))

    def test_legitimate_imports_and_uses_are_allowed(self):
        source = "import inspect\nimport gc\na = inspect.signature(callback)\nb = gc.collect()\nc = env.pilot_history\n"
        self.assertEqual(forbidden_patterns(source), [])
        self.assertEqual(forbidden_patterns('documented_name = "__closure__"'), [])

    def test_project_python_sources_have_no_concrete_bypass_patterns(self):
        result = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "*.py"],
            capture_output=True, timeout=30, check=True,
        )
        findings = []
        for raw in sorted(set(result.stdout.split(b"\0")) - {b""}):
            name = raw.decode("utf-8")
            path = ROOT / name
            if name in PARTICIPANT_SOURCES:
                # Organizer implementations contain evaluator-only internals.
                # The separate integrity test requires exact supplied bytes.
                continue
            if not path.exists():
                # A tracked file removed by this change is not executable input.
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(ROOT):
                findings.append((name, 0, "unscanned-source-link"))
                continue
            findings.extend((name, line, kind) for line, kind in forbidden_patterns(path.read_text(encoding="utf-8-sig")))
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
