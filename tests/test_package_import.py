"""Import must not publish unrelated ZIP entries or replace local datasets."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from scripts.import_participant_package import DATA_FILES, SOURCE_FILES, import_package


class PackageImportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "project"
        self.root.mkdir()
        self.files = {name: ("fixture: " + name).encode() for name in SOURCE_FILES + DATA_FILES}
        for name in SOURCE_FILES:
            (self.root / name).write_bytes(self.files[name])
        self.manifest = self.base / "manifest.json"
        self.manifest.write_text(json.dumps({"files": {name: hashlib.sha256(data).hexdigest()
                                                      for name, data in self.files.items()}}))

    def package(self, prefix="", corrupt=None):
        target = self.base / "participant.zip"
        with zipfile.ZipFile(target, "w") as archive:
            for name, data in self.files.items():
                archive.writestr(prefix + name, b"changed" if name == corrupt else data)
            archive.writestr("../unexpected.txt", b"unrelated")
        return target

    def test_nested_zip_imports_only_verified_manifest_data(self):
        import_package(self.package("participant/"), self.root, self.manifest)
        for name, data in self.files.items():
            self.assertEqual((self.root / name).read_bytes(), data)
        self.assertFalse((self.base / "unexpected.txt").exists())
        self.assertEqual({p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file()},
                         set(self.files))
        import_package(None, self.root, self.manifest)

    def test_invalid_source_copies_no_data(self):
        with self.assertRaisesRegex(ValueError, "Missing or modified"):
            import_package(self.package(corrupt=DATA_FILES[-1]), self.root, self.manifest)
        self.assertTrue(all(not (self.root / name).exists() for name in DATA_FILES))

    def test_different_local_data_is_preserved_before_other_files_are_copied(self):
        existing = self.root / DATA_FILES[-1]
        existing.parent.mkdir()
        existing.write_bytes(b"user data")
        with self.assertRaisesRegex(ValueError, "Refusing to overwrite"):
            import_package(self.package(), self.root, self.manifest)
        self.assertEqual(existing.read_bytes(), b"user data")
        self.assertFalse((self.root / DATA_FILES[0]).exists())

    def test_modified_official_source_blocks_import(self):
        (self.root / SOURCE_FILES[0]).write_bytes(b"changed evaluator")
        with self.assertRaisesRegex(ValueError, "Missing or modified"):
            import_package(self.package(), self.root, self.manifest)
        self.assertFalse((self.root / DATA_FILES[0]).exists())

    def test_ambiguous_packages_are_rejected(self):
        package = self.package()
        with zipfile.ZipFile(package, "a") as archive:
            archive.writestr("other/customer_profile.csv", b"another package")
        with self.assertRaisesRegex(ValueError, "exactly one"):
            import_package(package, self.root, self.manifest)


if __name__ == "__main__":
    unittest.main()
