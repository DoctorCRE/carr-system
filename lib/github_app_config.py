"""GitHub App configuration shared by authentication and named key intake."""

import json
import re
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parents[1] / "ops/config/github-app.json"


def read_config(path: Path) -> dict:
    """Read validated app settings without loading credentials or dependencies."""
    try:
        config = json.loads(path.read_text())
        if not re.fullmatch(r"[0-9]+", str(config["app_id"])):
            raise ValueError
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", config["repository"]):
            raise ValueError
        filename = config["key_file"]
        if not isinstance(filename, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", filename):
            raise ValueError
        if filename in {".", ".."}:
            raise ValueError
        if type(config.get("reserve", 500)) is not int or config.get("reserve", 500) < 0:
            raise ValueError
        return config
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError("GitHub App configuration is invalid") from None


if __name__ == "__main__":
    try:
        print(read_config(CONFIG_PATH)["key_file"])
    except ValueError:
        raise SystemExit(1) from None
