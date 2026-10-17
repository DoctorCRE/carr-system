"""Dedicated GitHub App credentials and budget for background gh subprocesses.

Call gh_env once, pass that environment to remaining_budget and every gh call,
and stop when its budget is paused. Missing setup uses the stored gh login with
a warning; app authentication and budget failures raise GitHubAppError.
"""

from __future__ import annotations

import fcntl
import hashlib
import http.client
import json
import logging
import os
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import jwt

from lib.github_app_config import CONFIG_PATH, read_config

LOG = logging.getLogger(__name__)
API = "https://api.github.com"


class GitHubAppError(RuntimeError):
    """A sanitized failure that must stop background GitHub calls."""


class _MissingInstallation(Exception):
    pass


@dataclass(frozen=True)
class GitHubBudget:
    core: int
    graphql: int
    reserve: int
    reset: int | None

    @property
    def paused(self) -> bool:
        return self.reset is not None

    @property
    def message(self) -> str:
        if self.reset is None:
            return "GitHub budget available"
        reset = datetime.fromtimestamp(self.reset, timezone.utc).isoformat()
        return f"paused until {reset}"


def _config() -> dict:
    try:
        return read_config(CONFIG_PATH)
    except ValueError:
        raise GitHubAppError("GitHub App configuration is invalid; GitHub calls stopped") from None


def _request(path: str, bearer: str, *, post: bool = False) -> dict:
    request = urllib.request.Request(API + path, data=b"{}" if post else None,
        headers={"Authorization": f"Bearer {bearer}",
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2026-03-10",
                 "User-Agent": "carr-background-github-app",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
        if not isinstance(payload, dict):
            raise ValueError
        return payload
    except urllib.error.HTTPError as error:
        if error.code == 404 and not post:
            raise _MissingInstallation from None
        raise GitHubAppError("GitHub App API request failed; GitHub calls stopped") from None
    except (OSError, ValueError, TypeError, http.client.HTTPException):
        raise GitHubAppError("GitHub App API request failed; GitHub calls stopped") from None


def _read_cache(path: Path, identity: str, now: float) -> str | None:
    try:
        with path.open() as source:
            if stat.S_IMODE(os.fstat(source.fileno()).st_mode) != 0o600:
                return None
            cached = json.load(source)
        if (cached["identity"] == identity and cached["expires_at"] - 300 > now
                and isinstance(cached["token"], str) and cached["token"]):
            return cached["token"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def _token(config: dict, key: bytes) -> str:
    cache_dir = Path.home() / ".cache/carr"
    cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    identity = hashlib.sha256(json.dumps(config, sort_keys=True).encode() + key).hexdigest()
    cache = cache_dir / "github-app-token.json"
    lock = cache_dir / "github-app-token.lock"
    with os.fdopen(os.open(lock, os.O_CREAT | os.O_RDWR, 0o600), "r+") as locked:
        os.fchmod(locked.fileno(), 0o600)
        fcntl.flock(locked, fcntl.LOCK_EX)
        now = time.time()
        cached = _read_cache(cache, identity, now)
        if cached:
            return cached
        signed = jwt.encode({"iat": int(now) - 60, "exp": int(now) + 540,
                             "iss": str(config["app_id"])}, key, algorithm="RS256")
        installation = _request(f"/repos/{config['repository']}/installation", signed)
        if installation.get("suspended_at"):
            raise _MissingInstallation
        installation_id = installation.get("id")
        if type(installation_id) is not int or installation_id <= 0:
            raise GitHubAppError("GitHub App installation response is invalid; GitHub calls stopped")
        result = _request(f"/app/installations/{installation_id}/access_tokens", signed, post=True)
        try:
            token = result["token"]
            expiry = datetime.fromisoformat(result["expires_at"].replace("Z", "+00:00"))
            if not isinstance(token, str) or not token or expiry.tzinfo is None:
                raise ValueError
            expires_at = expiry.timestamp()
            if expires_at <= now + 300:
                raise ValueError
        except (ValueError, KeyError, TypeError, AttributeError):
            raise GitHubAppError("GitHub App token response is invalid; GitHub calls stopped") from None
        with tempfile.NamedTemporaryFile(mode="w", dir=cache_dir, prefix="github-app-token-",
                                         delete=False) as output:
            os.fchmod(output.fileno(), 0o600)
            json.dump({"identity": identity, "token": token, "expires_at": expires_at}, output)
            output.flush()
            os.fsync(output.fileno())
            temporary = Path(output.name)
        os.replace(temporary, cache)
        return token


def gh_env() -> dict[str, str]:
    """Copy the process environment, selecting app auth or explicit setup fallback."""
    env = os.environ.copy()
    env.pop("GH_TOKEN", None)
    env.pop("GITHUB_TOKEN", None)
    env["GH_HOST"] = "github.com"
    env["CARR_GITHUB_BUDGET_PRINCIPAL"] = "stored-login"
    config = _config()
    key_path = Path.home() / ".config/carr" / config["key_file"]
    try:
        with key_path.open("rb") as source:
            if stat.S_IMODE(os.fstat(source.fileno()).st_mode) & 0o077:
                raise GitHubAppError("GitHub App private key permissions are unsafe; GitHub calls stopped")
            key = source.read()
    except FileNotFoundError:
        LOG.warning("GitHub App private key missing; falling back to user login")
        return env
    except OSError:
        raise GitHubAppError("GitHub App private key unavailable; GitHub calls stopped") from None
    try:
        env["GH_TOKEN"] = _token(config, key)
        env["CARR_GITHUB_BUDGET_PRINCIPAL"] = f"app:{config['app_id']}:{config['repository']}"
    except _MissingInstallation:
        LOG.warning("GitHub App installation missing or suspended; falling back to user login")
    except GitHubAppError:
        raise
    except (OSError, ValueError, jwt.PyJWTError):
        raise GitHubAppError("GitHub App authentication failed; GitHub calls stopped") from None
    return env


def remaining_budget(*, env: dict[str, str] | None = None, reserve: int | None = None) -> GitHubBudget:
    """Read both pools using the same gh environment the caller will consume."""
    floor = _config().get("reserve", 500) if reserve is None else reserve
    if type(floor) is not int or floor < 0:
        raise GitHubAppError("GitHub budget reserve must be a nonnegative integer")
    if env is None:
        env = gh_env()
    try:
        result = subprocess.run(["gh", "api", "rate_limit"], env=env,
                                capture_output=True, text=True, timeout=20)
        if result.returncode:
            raise ValueError
        resources = json.loads(result.stdout)["resources"]
        pools = [resources["core"], resources["graphql"]]
        for pool in pools:
            if any(type(pool[name]) is not int or pool[name] < 0 for name in ("remaining", "reset")):
                raise ValueError
            datetime.fromtimestamp(pool["reset"], timezone.utc)
        reset = max((pool["reset"] for pool in pools if pool["remaining"] < floor), default=None)
        return GitHubBudget(pools[0]["remaining"], pools[1]["remaining"], floor, reset)
    except (OSError, ValueError, OverflowError, KeyError, TypeError, subprocess.SubprocessError):
        raise GitHubAppError("GitHub budget read failed; GitHub calls stopped") from None
