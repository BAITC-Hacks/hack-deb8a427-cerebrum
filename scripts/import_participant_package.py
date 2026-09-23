"""Restore the user-supplied organizer datasets and verify package provenance."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = Path(__file__).with_name("participant_package_manifest.json")
DATA_FILES = (
    "customer_profile.csv", "tariff_dictionary.csv", "feature_dictionary.csv",
    "data/change_tariff.csv", "data/traffic.csv", "data/arpu_monthly.csv", "data/dict_tariff.csv",
)
SOURCE_FILES = (
    "environment.py", "mock_environment.py", "scoring_core.py", "local_eval.py",
    "make_submission.py", "agent_template.py", "PARTICIPANT_GUIDE.md",
)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify(directory, names, expected):
    for name in names:
        path = directory / name
        if not path.is_file() or digest(path) != expected[name]:
            raise ValueError(f"Missing or modified participant file: {name}")


def import_package(source, root=ROOT, manifest=MANIFEST):
    """Validate the entire package before copying CSV; accept a folder or ZIP."""
    expected = json.loads(manifest.read_text(encoding="utf-8"))["files"]
    if set(expected) != set(SOURCE_FILES + DATA_FILES):
        raise ValueError("Unexpected participant manifest entries")
    verify(root, SOURCE_FILES, expected)
    with tempfile.TemporaryDirectory(prefix="cerebrum-import-") as temporary:
        if source and source.is_file():
            # Extract only manifest entries to controlled paths. Unrelated
            # members, symlinks and traversal names never become local files.
            with zipfile.ZipFile(source) as archive:
                matches = [name[:-len("customer_profile.csv")] for name in archive.namelist()
                           if name == "customer_profile.csv" or name.endswith("/customer_profile.csv")]
                if len(matches) != 1:
                    raise ValueError("ZIP must contain exactly one participant package")
                prefix = matches[0]
                for name in expected:
                    member = prefix + name
                    if archive.namelist().count(member) != 1:
                        raise ValueError(f"Missing or duplicate participant file: {name}")
                    info = archive.getinfo(member)
                    if info.file_size > 100_000_000:
                        raise ValueError("Participant file is too large")
                    target = Path(temporary) / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as incoming, target.open("wb") as outgoing:
                        shutil.copyfileobj(incoming, outgoing)
            source = Path(temporary)
        if source:
            verify(source, SOURCE_FILES + DATA_FILES, expected)
            for name in DATA_FILES:
                target = root / name
                if target.exists() and (not target.is_file() or digest(target) != expected[name]):
                    raise ValueError(f"Refusing to overwrite a different local dataset: {name}")
                if not target.resolve().is_relative_to(root.resolve()) or target.is_symlink():
                    raise ValueError(f"Refusing a linked dataset path: {name}")
            for name in DATA_FILES:
                target = root / name
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source / name, target)
        verify(root, DATA_FILES, expected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--source", type=Path, help="Directory or ZIP of the supplied participant package")
    mode.add_argument("--check", action="store_true", help="Verify the installed package without changing files")
    args = parser.parse_args()
    try:
        import_package(args.source.resolve() if args.source else None)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError):
            print(f"FAIL {exc}")
        else:
            print("FAIL participant package is unavailable or its manifest cannot be read")
        return 1
    print(f"PASS unchanged organizer sources ({len(SOURCE_FILES)}) and local datasets ({len(DATA_FILES)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
