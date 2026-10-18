#!/usr/bin/env python3
"""Link the latest failed main CI run without replaying checks."""
import subprocess


def main():
    try:
        result = subprocess.run(
            ["gh", "run", "list", "--repo", "jbookout/carr-system", "--branch", "main",
             "--workflow", "ci.yml", "--status", "failure", "--limit", "1", "--json", "url",
             "--jq", ".[0].url // empty"],
            capture_output=True, text=True, timeout=15)
        if result.returncode == 0 and result.stdout.strip():
            print("Latest failed main CI run: " + result.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass


if __name__ == "__main__":
    main()
