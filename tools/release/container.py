#!/usr/bin/env python3
"""Prepare verified release binaries and choose the highest published release."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import tarfile

VERSION = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
ARCHITECTURES = {
    "x86_64": {"platform": "amd64", "runner": "ubuntu-22.04"},
    "aarch64": {"platform": "arm64", "runner": "ubuntu-22.04-arm"},
}
FILES = {"tarn", "tarn-lsp", "README.md", "COPYING"}


def version(tag):
    match = VERSION.fullmatch(tag)
    if not match:
        raise ValueError(f"Expected vX.Y.Z release tag: {tag}")
    return tuple(map(int, match.groups()))


def highest_release(pages):
    # Include experimental prereleases: Tarn currently publishes only those.
    releases = [r for page in pages for r in page
                if not r.get("draft") and VERSION.fullmatch(r["tag_name"])]
    if not releases:
        raise ValueError("No published numeric Tarn release found")
    return max(releases, key=lambda r: version(r["tag_name"]))["tag_name"]


def release_matrix(release):
    tag = release["tag_name"]
    version(tag)
    if release.get("draft"):
        raise ValueError("Release is still a draft")
    names = {asset["name"] for asset in release["assets"]}
    include = [{"arch": arch, **config} for arch, config in ARCHITECTURES.items()
               if f"tarn-{tag[1:]}-linux-{arch}.tar.gz" in names]
    if not include or "SHA256SUMS" not in names:
        raise ValueError("Release has no supported toolchain archives or checksums")
    return {"include": include}


def prepare(tag, assets, context, arch="x86_64"):
    version(tag)
    if arch not in ARCHITECTURES:
        raise ValueError(f"Unsupported release architecture: {arch}")
    archive = assets / f"tarn-{tag[1:]}-linux-{arch}.tar.gz"
    lines = (assets / "SHA256SUMS").read_text().splitlines()
    expected = [line.split()[0] for line in lines
                if len(line.split()) == 2 and line.split()[1] == archive.name]
    if len(expected) != 1 or expected[0] != hashlib.sha256(archive.read_bytes()).hexdigest():
        raise ValueError("Release archive checksum mismatch or missing checksum")
    # Validate the complete inventory before writing any member. Never extract
    # archive-provided paths, links, devices or ownership metadata.
    with tarfile.open(archive, "r:gz") as bundle:
        members = bundle.getmembers()
        if len(members) != len(FILES) or {m.name for m in members} != FILES or not all(m.isfile() for m in members):
            raise ValueError("Unexpected release archive inventory")
        destination = context / "toolchain"
        destination.mkdir(parents=True, exist_ok=True)
        for member in members:
            target = destination / member.name
            with bundle.extractfile(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            target.chmod(0o755 if member.name in {"tarn", "tarn-lsp"} else 0o644)
    recipe = Path(__file__).resolve().parents[1] / "container"
    for name in ("Dockerfile", ".dockerignore"):
        shutil.copy2(recipe / name, context / name)


if __name__ == "__main__":
    if len(sys.argv) in (5, 6) and sys.argv[1] == "prepare":
        prepare(sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4]), *sys.argv[5:])
    elif len(sys.argv) == 2 and sys.argv[1] == "matrix":
        print(json.dumps(release_matrix(json.load(sys.stdin))))
    elif len(sys.argv) == 2 and sys.argv[1] == "highest":
        print(highest_release(json.load(sys.stdin)))
    else:
        sys.exit("Usage: container.py prepare vX.Y.Z ASSETS CONTEXT [ARCH] | matrix < release.json | highest < releases.json")
