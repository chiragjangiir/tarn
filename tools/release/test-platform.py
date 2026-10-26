#!/usr/bin/env python3
"""Architecture selection must agree in packaging and standalone installation."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ReleasePlatform(unittest.TestCase):
    def test_architecture_detection_and_download_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "uname").write_text('#!/bin/sh\nif [ "$1" = -s ]; then echo "$TEST_OS"; else echo "$TEST_ARCH"; fi\n')
            (root / "curl").write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$TEST_DOWNLOAD"\nexit 19\n')
            (root / "uname").chmod(0o755)
            (root / "curl").chmod(0o755)
            for system, machine, arch in [("Linux", "x86_64", "x86_64"),
                                          ("Linux", "aarch64", "aarch64"),
                                          ("Linux", "arm64", "aarch64"),
                                          ("Linux", "armv7l", None),
                                          ("Darwin", "arm64", None)]:
                with self.subTest(system=system, machine=machine):
                    download = root / "download"
                    download.unlink(missing_ok=True)
                    env = dict(os.environ, PATH=f"{root}:{os.environ['PATH']}",
                               TEST_OS=system, TEST_ARCH=machine, TEST_DOWNLOAD=str(download))
                    result = subprocess.run(["sh", str(ROOT / "tools/release/platform.sh")], env=env, capture_output=True)
                    self.assertEqual(result.returncode == 0, arch is not None)
                    if arch:
                        self.assertEqual(result.stdout.decode().strip(), arch)
                    install = subprocess.run(["sh", str(ROOT / "tools/release/install.sh"),
                                              "--version", "0.0.8", "--prefix", str(root / "bin")],
                                             env=env, capture_output=True)
                    if arch:
                        self.assertEqual(install.returncode, 19)
                        self.assertIn(f"tarn-0.0.8-linux-{arch}.tar.gz", download.read_text())
                    else:
                        self.assertNotEqual(install.returncode, 0)
                        self.assertFalse(download.exists())


if __name__ == "__main__":
    unittest.main()
