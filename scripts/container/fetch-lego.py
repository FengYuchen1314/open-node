"""Fetch only the pinned, checksum-verified lego binary and its license."""

import hashlib
import io
import sys
import tarfile
from pathlib import Path
from urllib.request import urlopen

VERSION = "4.35.2"
DIGESTS = {
    "amd64": "ee5be4bf457de8e3efa86a51651c75c87f0ee0e4e9f3ae14f6034d68365770f3",
    "arm64": "e1f153179098d27ce044aaaa168c0e323d50ae71b0f1a147aa8ae49ac6b14d89",
}


def main():
    arch, destination = sys.argv[1], Path(sys.argv[2])
    if arch not in DIGESTS:
        raise SystemExit("Supported image architectures: amd64, arm64")
    url = f"https://github.com/go-acme/lego/releases/download/v{VERSION}/lego_v{VERSION}_linux_{arch}.tar.gz"
    with urlopen(url, timeout=90) as response:
        data = response.read(150_000_001)
    if len(data) > 150_000_000 or hashlib.sha256(data).hexdigest() != DIGESTS[arch]:
        raise SystemExit("lego archive checksum mismatch")
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for name in ("lego", "LICENSE"):
            member = archive.getmember(name)
            if not member.isfile() or member.size > 250_000_000:
                raise SystemExit("Unexpected lego archive member")
            with archive.extractfile(member) as source:
                target = destination / name
                target.write_bytes(source.read())
                target.chmod(0o755 if name == "lego" else 0o644)


if __name__ == "__main__":
    main()
