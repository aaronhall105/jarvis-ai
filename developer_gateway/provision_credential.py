"""Explicit local credential provisioning. Never prints values or copies other Core settings."""

import argparse
import os
from pathlib import Path


def provision(source: Path, destination: Path) -> None:
    if source.is_symlink() or source.stat().st_mode & 0o077:
        raise ValueError("Source must be an owner-private regular file")
    value = ""
    for line in source.read_text().splitlines():
        name, separator, candidate = line.partition("=")
        if separator and name.strip() == "OPENAI_API_KEY":
            value = candidate.strip().strip("'\"")
            break
    if not value:
        raise ValueError("Source has no API credential")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Exclusive creation: never overwrite an existing Developer credential.
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write(value + "\n")
        output.flush()
        os.fsync(output.fileno())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-env", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    provision(args.source_env, args.destination)
    print("Developer API credential provisioned; no other settings copied.")
