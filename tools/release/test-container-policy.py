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

    def test_release_matrix_discovers_both_architectures_and_supports_old_releases(self):
        release = {"tag_name": "v0.0.8", "assets": [
            {"name": "tarn-0.0.8-linux-x86_64.tar.gz"},
            {"name": "tarn-0.0.8-linux-aarch64.tar.gz"}, {"name": "SHA256SUMS"}]}
        matrix = policy.release_matrix(release)["include"]
        self.assertEqual([item["platform"] for item in matrix], ["amd64", "arm64"])
        self.assertEqual(matrix[1]["runner"], "ubuntu-22.04-arm")
        release["assets"].pop(1)
        self.assertEqual(len(policy.release_matrix(release)["include"]), 1)
        release["draft"] = True
        with self.assertRaises(ValueError):
            policy.release_matrix(release)
        release.pop("draft")
        release["assets"] = [{"name": "SHA256SUMS"}]
        with self.assertRaises(ValueError):
            policy.release_matrix(release)

    def test_arm_archive_is_selected_from_shared_checksums(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "tarn-0.0.8-linux-aarch64.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                for name in policy.FILES:
                    member = tarfile.TarInfo(name)
                    member.size = 3
                    bundle.addfile(member, io.BytesIO(b"arm"))
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            checksum = f"{digest}  {archive.name}\n"
            (root / "SHA256SUMS").write_text(
                f"{'0' * 64}  tarn-0.0.8-linux-x86_64.tar.gz\n" + checksum)
            policy.prepare("v0.0.8", root, root / "context", "aarch64")
            self.assertEqual((root / "context/toolchain/tarn").read_bytes(), b"arm")
            with self.assertRaisesRegex(ValueError, "Unsupported"):
                policy.prepare("v0.0.8", root, root / "other", "armv7l")
            (root / "SHA256SUMS").write_text(checksum + checksum)
            with self.assertRaisesRegex(ValueError, "checksum"):
                policy.prepare("v0.0.8", root, root / "duplicate", "aarch64")

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
