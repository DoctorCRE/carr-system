#!/usr/bin/env python3
"""Export the committed marketplace into a retained, commit-addressed bundle."""
import argparse
import io
from pathlib import Path
import subprocess
import tarfile
import tempfile


def bundle(destination):
    repo = Path(__file__).resolve().parents[3]
    destination = destination.expanduser().resolve()
    ancestor = destination
    while not ancestor.exists():
        ancestor = ancestor.parent
    inside_git = subprocess.run(["git", "-C", str(ancestor), "rev-parse", "--show-toplevel"],
                                capture_output=True)
    if inside_git.returncode == 0:
        raise SystemExit("Keep installed marketplace bundles outside Git checkouts.")
    revision = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    archive = subprocess.check_output(["git", "-C", str(repo), "archive", "--format=tar", revision,
                                       ".claude-plugin/marketplace.json", "plugins/pstack"])
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        files = {}
        for member in source.getmembers():
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts or not (member.isfile() or member.isdir()):
                raise SystemExit(f"Unsupported marketplace archive entry: {member.name}")
            if member.isfile():
                files[member.name] = source.extractfile(member).read()
        target = destination / revision
        if target.exists() or target.is_symlink():
            runtime_cache = target / "plugins/pstack/skills/poteto-mode/scripts/node_modules"
            source_paths = [path for path in target.rglob("*")
                            if path != runtime_cache and runtime_cache not in path.parents]
            actual = {str(path.relative_to(target)) for path in source_paths if path.is_file()}
            if (target.is_symlink() or actual != set(files)
                    or any(path.is_symlink() for path in source_paths)
                    or any((target / name).read_bytes() != data for name, data in files.items())):
                raise SystemExit("Existing bundle differs from committed source; preserved.")
        else:
            destination.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=destination) as tmp:
                staged = Path(tmp) / revision
                staged.mkdir()
                source.extractall(staged, filter="data")
                staged.rename(target)
        return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", nargs="?", type=Path,
                        default=Path.home() / ".local/share/carr/pstack-marketplaces")
    args = parser.parse_args()
    print(bundle(args.destination))
