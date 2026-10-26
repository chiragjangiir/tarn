#!/usr/bin/env python3
"""Regression tests for latest selection and untrusted release inventories."""
import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("container_release", Path(__file__).with_name("container.py"))
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


class ContainerPolicy(unittest.TestCase):
    def test_latest_uses_numeric_order_includes_prereleases_and_ignores_drafts(self):
        pages = [[{"tag_name": "v0.0.9"}, {"tag_name": "v0.0.10", "prerelease": True}],
                 [{"tag_name": "v1.0.0", "draft": True}, {"tag_name": "nightly"}]]
        self.assertEqual(policy.highest_release(pages), "v0.0.10")
        # Retrying an old version cannot make it the candidate for latest.
        self.assertNotEqual(policy.highest_release(pages), "v0.0.9")

    def test_invalid_or_missing_versions_fail_closed(self):
        for tag in ["main", "v01.0.0", "v0.0.6-rc1", "v0.0.6\n"]:
            with self.assertRaises(ValueError):
                policy.version(tag)
        with self.assertRaises(ValueError):
            policy.highest_release([[]])

    def test_checksum_paths_links_and_duplicate_members_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "tarn-0.0.6-linux-x86_64.tar.gz"
            inventories = [["../tarn", "tarn-lsp", "README.md", "COPYING"],
                           ["tarn", "tarn", "README.md", "COPYING"],
                           ["tarn", "tarn-lsp", "README.md", "COPYING"]]
            for names in inventories:
                with tarfile.open(archive, "w:gz") as bundle:
                    for name in names:
                        member = tarfile.TarInfo(name)
                        if name == "tarn-lsp":
                            member.type = tarfile.SYMTYPE
                            member.linkname = "/etc/passwd"
                            bundle.addfile(member)
                        else:
                            member.size = 1
                            bundle.addfile(member, io.BytesIO(b"x"))
                digest = hashlib.sha256(archive.read_bytes()).hexdigest()
                (root / "SHA256SUMS").write_text(f"{digest}  {archive.name}\n")
                with self.assertRaisesRegex(ValueError, "inventory"):
                    policy.prepare("v0.0.6", root, root / "context")
                self.assertFalse((root / "context").exists())
            (root / "SHA256SUMS").write_text(f"{'0' * 64}  {archive.name}\n")
            with self.assertRaisesRegex(ValueError, "checksum"):
                policy.prepare("v0.0.6", root, root / "context")


if __name__ == "__main__":
    unittest.main()
