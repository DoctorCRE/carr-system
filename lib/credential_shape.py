"""Token syntax shared by saving, health probes, and remote authentication."""
import json
from pathlib import Path
import re

INVENTORY = Path(__file__).resolve().parents[1] / "ops/config/credential-inventory.v1.json"


def claude_token_shape() -> dict:
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    return next(c["probe"] for c in inventory["credentials"]
                if c["probe"]["type"] == "claude_cli_token_age")


def valid_claude_token(value: str) -> bool:
    spec = claude_token_shape()
    return (len(value) == spec["expected_length"]
            and value.startswith(spec["expected_prefix"])
            and re.fullmatch(spec["allowed_characters"], value, flags=re.ASCII) is not None)


def claude_token_hint(value: str) -> str:
    spec = claude_token_shape()
    return (f"expected {spec['expected_length']} characters starting {spec['expected_prefix']} "
            f"using letters, digits, _ or -; got {len(value)} characters")
