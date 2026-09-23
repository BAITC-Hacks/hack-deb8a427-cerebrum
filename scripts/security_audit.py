"""Offline heuristic audit of current Git text, reachable history and dependencies."""

import ast
import csv
import importlib.metadata
import io
from pathlib import Path
import re
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.preflight import contains_secret, repository_paths


def git(root, *args, data=None, check=True):
    return subprocess.run(["git", "-C", str(root), *args], input=data,
                          capture_output=True, timeout=60, check=check)


def scan_text(path, content, personal_data=False):
    """Return categories only; callers never print content or matching values."""
    try:
        if b"\0" in content:
            return ["non-utf8-or-binary-not-scanned"]
        source = content.decode("utf-8-sig")
    except UnicodeError:
        return ["non-utf8-or-binary-not-scanned"]
    issues = []
    if contains_secret(source, Path(path).suffix.lower()):
        issues.append("possible-secret")
    if personal_data and Path(path).suffix.lower() == ".csv":
        header = next(csv.reader(io.StringIO(source)), [])
        sensitive = {"email", "phone", "phone_number", "msisdn", "iin", "full_name", "fio"}
        if sensitive.intersection(column.strip().lower() for column in header):
            issues.append("possible-personal-data-columns")
    return issues


def history_findings(root=ROOT):
    objects = git(root, "rev-list", "--objects", "--all").stdout.splitlines()
    labels = {}
    for row in objects:
        parts = row.split(b" ", 1)
        labels[parts[0]] = parts[1].decode("utf-8", errors="replace") if len(parts) == 2 else "commit-or-tag"
    metadata = git(root, "cat-file", "--batch-check", data=b"\n".join(labels) + b"\n").stdout.splitlines()
    findings, counts = [], {"text_blobs": 0, "commit_tag_objects": 0, "skipped_binary": 0}
    for row in metadata:
        oid, kind, _ = row.split()
        if kind not in {b"blob", b"commit", b"tag"}:
            continue
        label = labels[oid]
        content = git(root, "cat-file", kind.decode("ascii"), oid.decode("ascii")).stdout
        issues = scan_text(label if kind == b"blob" else "commit.txt", content)
        if "non-utf8-or-binary-not-scanned" in issues:
            counts["skipped_binary"] += 1
        else:
            counts["text_blobs" if kind == b"blob" else "commit_tag_objects"] += 1
        findings.extend((label, issue) for issue in issues)
    return findings, counts


def requirement_findings(text, imported, installed):
    findings, versions = [], []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+-]+)", line.strip())
        if not match:
            findings.append(("requirements.txt", "unpinned-or-unsupported-requirement"))
            continue
        name, pinned = match.groups()
        actual = installed(name)
        versions.append((name, pinned, actual))
        if name.replace("-", "_") not in imported:
            findings.append(("requirements.txt", "dependency-without-direct-import"))
        if actual != pinned:
            findings.append(("requirements.txt", "installed-version-mismatch"))
    return findings, versions


def audit(root=ROOT):
    tracked, paths = repository_paths(root)
    findings, imported = [], set()
    counts = {"current_text": 0, "tracked_logs": 0, "skipped_binary": 0}
    for name in sorted(paths):
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            findings.append((name, "unscanned-link"))
            continue
        filename = path.name.lower()
        if filename == ".env" or filename.startswith(".env.") and filename != ".env.example":
            findings.append((name, "env-file-in-git-file-list"))
            continue
        if (filename in {"credentials.json", "id_rsa", "id_ed25519"}
                or filename.endswith((".key", ".private.pem"))
                or filename.startswith(("credentials.local.", "service-account"))
                or {".credentials", ".aws", ".ssh"}.intersection(path.relative_to(root).parts)):
            findings.append((name, "possible-credentials-file"))
        content = path.read_bytes()
        issues = scan_text(name, content, personal_data=True)
        findings.extend((name, issue) for issue in issues)
        if "non-utf8-or-binary-not-scanned" in issues:
            counts["skipped_binary"] += 1
            continue
        counts["current_text"] += 1
        counts["tracked_logs"] += name in tracked and path.suffix.lower() == ".log"
        source = content.decode("utf-8-sig")
        if filename == ".env.example":
            for line in source.splitlines():
                if line.strip() and not line.lstrip().startswith("#"):
                    parts = line.split("=", 1)
                    if len(parts) != 2 or parts[1].strip():
                        findings.append((name, "nonempty-env-example-value"))
        if path.suffix.lower() == ".py":
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".")[0])

    probes = [".env", ".env.local", ".venv/example", "venv/example", "__pycache__/example.pyc",
              ".idea/workspace.xml", ".vscode/settings.json", "logs/example.log",
              "test-results/example.xml", "credentials.json"]
    ignored = git(root, "check-ignore", "--no-index", *probes, check=False)
    if ignored.returncode not in (0, 1):
        findings.append((".gitignore", "ignore-check-failed"))
    elif set(ignored.stdout.decode("utf-8").splitlines()) != set(probes):
        findings.append((".gitignore", "missing-local-file-protection"))
    template = git(root, "check-ignore", "--no-index", ".env.example", check=False)
    if template.returncode != 1:
        findings.append((".gitignore", "env-example-not-trackable"))

    history, history_counts = history_findings(root)
    findings.extend(history)

    def installed(name):
        try:
            return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            return "missing"

    dependency_issues, versions = requirement_findings(
        (root / "requirements.txt").read_text(encoding="utf-8-sig"), imported, installed,
    )
    findings.extend(dependency_issues)
    return sorted(set(findings)), counts, history_counts, versions


def main():
    try:
        findings, current, history, versions = audit()
    except (OSError, UnicodeError, ValueError, SyntaxError, subprocess.SubprocessError):
        print("FAIL repository: audit-incomplete; raw details suppressed")
        return 1
    for name, issue in findings:
        status = "NOT RUN" if issue == "non-utf8-or-binary-not-scanned" else "FAIL"
        print(f"{status} {name}: {issue}")
    print(f"INFO current_text={current['current_text']} tracked_logs={current['tracked_logs']}")
    print(f"INFO history_text_blobs={history['text_blobs']} history_commit_tag_objects={history['commit_tag_objects']}")
    for name, pinned, actual in versions:
        print(f"INFO dependency={name} pinned={pinned} installed={actual}")
    print("INFO gitleaks=" + ("available-run-separately" if shutil.which("gitleaks") else "not-found"))
    print("NOT RUN vulnerability databases; no external systems scanned")
    print("PASS heuristic audit" if not findings else "FAIL audit incomplete or findings require review")
    return int(bool(findings))


if __name__ == "__main__":
    raise SystemExit(main())
