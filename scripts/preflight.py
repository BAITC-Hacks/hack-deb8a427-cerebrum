"""Read-only, standard-library repository preflight. No network requests."""

import ast
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {".py", ".md", ".rst", ".txt", ".json", ".toml", ".yml", ".yaml", ".ini", ".cfg", ".log", ".csv"}
SECRET_PATTERNS = (
    re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)\bBearer [A-Za-z0-9_.-]{20,}"),
)
SENSITIVE_NAME = re.compile(r"(?i)(?:^|_)(?:api_key|access_token|password|secret|token)$")
LITERAL_ASSIGNMENT = re.compile(
    r"(?im)^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*[\"']?([^\s\"'#]+)"
)


@dataclass(frozen=True)
class Result:
    status: str
    path: str
    kind: str


def repository_paths(root):
    def names(*args):
        result = subprocess.run(["git", "-C", str(root), "ls-files", "-z", *args],
                                capture_output=True, check=True, timeout=30)
        return {name.decode("utf-8", errors="surrogateescape") for name in result.stdout.split(b"\0") if name}
    tracked = names("--cached")
    return tracked, tracked | names("--others", "--exclude-standard")


def contains_secret(source, suffix):
    if any(pattern.search(source) for pattern in SECRET_PATTERNS):
        return True
    if suffix == ".py":
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return False  # Syntax of the entry point is checked separately.
        for node in ast.walk(tree):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
            value = node.value if targets else None
            if isinstance(value, ast.Constant) and isinstance(value.value, str) and value.value.strip():
                if any(isinstance(target, ast.Name) and SENSITIVE_NAME.search(target.id) for target in targets):
                    return True
    else:
        for name, value in LITERAL_ASSIGNMENT.findall(source):
            if SENSITIVE_NAME.search(name) and value.lower() not in {"none", "null"} and not value.startswith(("$", "<")):
                return True
    return False


def has_agent_act(source):
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Agent":
            for method in node.body:
                if isinstance(method, ast.FunctionDef) and method.name == "act":
                    if len(method.args.posonlyargs + method.args.args) >= 2:
                        return True
    return False


def check_repository(root=ROOT):
    results = []
    try:
        tracked, names = repository_paths(root)
    except (OSError, subprocess.SubprocessError):
        return [Result("FAIL", ".", "git-file-list-unavailable")]

    def local_file(path):
        return not path.is_symlink() and path.resolve().is_relative_to(root.resolve()) and path.is_file()

    for name, kind in (("agent.py", "agent-present"), ("README.md", "readme-present"),
                       ("local_eval.py", "evaluation-command-present")):
        path = root / name
        results.append(Result("PASS" if local_file(path) and path.stat().st_size else "FAIL", name, kind))
    try:
        path = root / "agent.py"
        valid = local_file(path) and has_agent_act(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, SyntaxError):
        valid = False
    results.append(Result("PASS" if valid else "FAIL", "agent.py", "Agent.act-declared"))
    try:
        path = root / "README.md"
        documented = local_file(path) and "python local_eval.py" in path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        documented = False
    results.append(Result("PASS" if documented else "FAIL", "README.md", "evaluation-command-documented"))

    scanned, scan_failed = 0, False
    for name in sorted(names):
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            results.append(Result("FAIL", name, "unscanned-link"))
            scan_failed = True
            continue
        filename = path.name.lower()
        if filename == ".env" or filename.startswith(".env.") and filename != ".env.example":
            results.append(Result("FAIL", name, "env-file-in-git-file-list"))
            scan_failed = True
            continue
        try:
            content = path.read_bytes()
            if b"\0" in content:
                raise UnicodeError()
            source = content.decode("utf-8-sig")
        except (OSError, UnicodeError):
            if path.suffix.lower() in TEXT_SUFFIXES or not path.exists() or filename == ".env.example":
                results.append(Result("FAIL", name, "unreadable-text"))
                scan_failed = True
            else:
                results.append(Result("SKIP", name, "binary-or-non-utf8-file"))
            continue
        scanned += 1
        if contains_secret(source, path.suffix.lower()):
            results.append(Result("FAIL", name, "possible-secret"))
            scan_failed = True
    if not scan_failed:
        results.append(Result("PASS", ".", "current-text-secret-scan" if scanned else "no-text-files"))

    submission = root / "submission.csv"
    if submission.exists() or "submission.csv" in tracked:
        present = local_file(submission) and submission.stat().st_size > 0
        results.append(Result("PASS" if present else "FAIL", "submission.csv", "generated-submission-present"))
    else:
        results.append(Result("SKIP", "submission.csv", "not-generated"))
    return results


def main():
    try:
        results = check_repository()
    except (OSError, ValueError):
        results = [Result("FAIL", ".", "preflight-could-not-complete")]
    for result in results:
        # Never display source, matching values, exception messages or environment.
        print(f"{result.status} {result.path}: {result.kind}")
    return int(any(result.status == "FAIL" for result in results))


if __name__ == "__main__":
    raise SystemExit(main())
