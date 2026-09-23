"""Restore the user-supplied organizer datasets and verify package provenance."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--source", type=Path, help="Directory of the supplied participant package")
    mode.add_argument("--check", action="store_true", help="Verify the installed package without changing files")
    args = parser.parse_args()
    try:
        expected = json.loads(MANIFEST.read_text(encoding="utf-8"))["files"]
        if set(expected) != set(SOURCE_FILES + DATA_FILES):
            raise ValueError("Unexpected participant manifest entries")
        verify(ROOT, SOURCE_FILES, expected)
        if args.source:
            source = args.source.resolve()
            verify(source, SOURCE_FILES + DATA_FILES, expected)
            # Validate all existing targets before writing any file.
            for name in DATA_FILES:
                target = ROOT / name
                if target.exists() and (not target.is_file() or digest(target) != expected[name]):
                    raise ValueError(f"Refusing to overwrite a different local dataset: {name}")
            for name in DATA_FILES:
                target = ROOT / name
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source / name, target)
        verify(ROOT, DATA_FILES, expected)
    except (OSError, ValueError, KeyError) as exc:
        if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError):
            print(f"FAIL {exc}")
        else:
            print("FAIL participant package is unavailable or its manifest cannot be read")
        return 1
    print(f"PASS unchanged organizer sources ({len(SOURCE_FILES)}) and local datasets ({len(DATA_FILES)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
