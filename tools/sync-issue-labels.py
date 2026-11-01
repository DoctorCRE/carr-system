#!/usr/bin/env python3
"""Apply the native issue label vocabulary using serial, paced gh calls."""
import argparse
import json
from pathlib import Path
import subprocess
import time


LABELS = Path(__file__).resolve().parents[1] / ".github" / "labels.json"


def sync_labels(repository, labels):
    for label in labels:
        time.sleep(2)
        subprocess.run([
            "gh", "label", "create", label["name"], "--repo", repository,
            "--color", label["color"], "--description", label["description"], "--force"
        ], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, choices=["DoctorCRE/carr-system"])
    args = parser.parse_args()
    sync_labels(args.repo, json.loads(LABELS.read_text()))


if __name__ == "__main__":
    main()
