"""Deliver the tracked call-check budget to launcher environments."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

SOURCE = Path(__file__).resolve().parents[1] / 'ops/config/call-budget.v1.json'


def daily_dollars() -> str:
    value = json.loads(SOURCE.read_text())['CALL_JEV_DAILY_DOLLARS']
    if not isinstance(value, str):
        raise ValueError('call budget must be configured as dollar text')
    return value


def configure_environment() -> None:
    # An explicit empty/invalid override must still reach the checker's hold.
    try:
        os.environ.setdefault('CALL_JEV_DAILY_DOLLARS', daily_dollars())
    except (OSError, ValueError, KeyError):
        pass


if __name__ == '__main__':
    configure_environment()
    os.execvpe(sys.argv[1], sys.argv[1:], os.environ)
