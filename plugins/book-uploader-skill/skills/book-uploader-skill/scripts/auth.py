"""
ProofBound authentication for CLI tools.

Browser ("device") sign-in — the user clicks a link and approves in the
browser; no password is ever typed into a terminal or chat. Handles the device
flow, token refresh, and credential storage. Talks only to the ProofBound API —
no Supabase-specific config needed.

Credentials stored at ~/.proofbound/credentials.json (shared across skills).

This is the CANONICAL copy. Every distributable skill gets it injected verbatim by
scripts/package-skill.sh; a checkout gets a symlink from `task skill:vendor`. Do not
edit a skill's scripts/auth.py -- edit this file.

It is byte-identical in every artifact because the only thing that ever varied
between the old hand-maintained copies was the entry-script name in three error
messages, and the file reads that for itself (see _ENTRY).
"""

from __future__ import annotations

import contextlib
import json
import sys
import time
import webbrowser
from pathlib import Path

import httpx

# The script the user actually invoked, so "run X login" names a command that
# exists. Read from argv rather than templated at injection time: that is what lets
# one byte-identical file serve every skill.
_ENTRY = Path(sys.argv[0]).name or "the skill script"

CREDENTIALS_DIR = Path.home() / ".proofbound"
CREDENTIALS_FILE = CREDENTIALS_DIR / "credentials.json"
CONFIG_FILE = CREDENTIALS_DIR / "config.yml"
# Holds an in-flight device authorization between `login` (start) and the poll.
DEVICE_PENDING_FILE = CREDENTIALS_DIR / "device_pending.json"

DEFAULT_API_URL = "https://api.proofbound.com"
AUTH_TIMEOUT = 30
DEFAULT_POLL_INTERVAL = 5


def get_api_url() -> str:
    """Resolve the ProofBound API base URL.

    Precedence: ``PROOFBOUND_API_URL`` env > the logged-in session's ``api_url``
    (credentials.json) > ``config.yml`` ``api_url`` > the production default.

    The session value is honored so requests reach the server that issued the
    token — ``get_token``/``logout`` already prefer it. Without this, a client
    logged in against a non-default endpoint would silently send its requests
    (with a token only valid elsewhere) to the production default.
    """
    import os

    url = os.environ.get("PROOFBOUND_API_URL")
    if url:
        return url.rstrip("/")

    creds = _load_credentials()
    if creds and creds.get("api_url"):
        return creds["api_url"].rstrip("/")

    if CONFIG_FILE.exists():
        import yaml

        config = yaml.safe_load(CONFIG_FILE.read_text()) or {}
        if config.get("api_url"):
            return config["api_url"].rstrip("/")

    return DEFAULT_API_URL


def _save_credentials(data: dict) -> None:
    """Save credentials to disk."""
    CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_FILE.write_text(json.dumps(data, indent=2))
    CREDENTIALS_FILE.chmod(0o600)


def _load_credentials() -> dict | None:
    """Load credentials from disk."""
    if not CREDENTIALS_FILE.exists():
        return None
    try:
        return json.loads(CREDENTIALS_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _refresh_token(base_url: str, refresh_token: str) -> dict:
    """Exchange refresh token for new token pair."""
    resp = httpx.post(
        f"{base_url}/auth/refresh",
        json={"refresh_token": refresh_token},
        timeout=AUTH_TIMEOUT,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Session expired. Please run: {_ENTRY} login")
    return resp.json()


def _save_pending(data: dict) -> None:
    CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
    DEVICE_PENDING_FILE.write_text(json.dumps(data, indent=2))
    DEVICE_PENDING_FILE.chmod(0o600)


def _load_pending() -> dict | None:
    if not DEVICE_PENDING_FILE.exists():
        return None
    try:
        return json.loads(DEVICE_PENDING_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def start_device_login(open_browser: bool = True) -> dict:
    """Begin browser sign-in.

    Asks the API for a device + user code, opens the verification URL in the
    user's browser (best-effort), persists the pending authorization, and
    returns the start payload. Call ``wait_for_device_login()`` next to poll.
    """
    base_url = get_api_url()
    resp = httpx.post(f"{base_url}/auth/device/start", timeout=AUTH_TIMEOUT)
    if resp.status_code != 200:
        raise RuntimeError(f"Could not start sign-in (HTTP {resp.status_code}): {resp.text}")

    data = resp.json()
    interval = int(data.get("interval") or DEFAULT_POLL_INTERVAL)
    expires_in = int(data.get("expires_in") or 600)
    _save_pending(
        {
            "device_code": data["device_code"],
            "interval": interval,
            "expires_at": time.time() + expires_in,
            "api_url": base_url,
        }
    )

    if open_browser:
        with contextlib.suppress(Exception):
            webbrowser.open(data["verification_uri_complete"])

    return data


def wait_for_device_login() -> dict:
    """Poll the pending device authorization until approved; store credentials.

    Returns the token response. Raises if there is no pending authorization or
    it expires / is denied before approval.
    """
    pending = _load_pending()
    if not pending:
        raise RuntimeError("No sign-in in progress. Run `login` first.")

    base_url = pending.get("api_url") or get_api_url()
    device_code = pending["device_code"]
    interval = int(pending.get("interval") or DEFAULT_POLL_INTERVAL)
    deadline = float(pending.get("expires_at") or (time.time() + 600))

    while time.time() < deadline:
        time.sleep(interval)
        try:
            resp = httpx.post(
                f"{base_url}/auth/device/poll",
                json={"device_code": device_code},
                timeout=AUTH_TIMEOUT,
            )
        except httpx.HTTPError:
            continue  # transient network error — keep polling
        if resp.status_code != 200:
            continue

        data = resp.json()
        status = data.get("status")
        if status == "approved":
            _save_credentials(
                {
                    "access_token": data["access_token"],
                    "refresh_token": data["refresh_token"],
                    "expires_at": time.time() + data["expires_in"] - 60,  # 60s safety margin
                    "api_url": base_url,
                }
            )
            with contextlib.suppress(OSError):
                DEVICE_PENDING_FILE.unlink()
            return data
        if status == "expired_token":
            with contextlib.suppress(OSError):
                DEVICE_PENDING_FILE.unlink()
            raise RuntimeError("Sign-in expired or was declined. Please run `login` again.")
        # authorization_pending / slow_down → keep polling

    raise RuntimeError("Sign-in timed out. Please run `login` again.")


def login(open_browser: bool = True) -> dict:
    """Convenience: start the device flow and block until approved.

    Used by humans in a real terminal and by tests. The CLI splits this into
    `login` (start) + `login-wait` (poll) so an agent can show the user the link
    before the polling blocks.
    """
    start_device_login(open_browser=open_browser)
    return wait_for_device_login()


def logout() -> None:
    """Invalidate refresh token and delete local credentials."""
    creds = _load_credentials()
    if creds and creds.get("refresh_token"):
        base_url = creds.get("api_url") or get_api_url()
        with contextlib.suppress(Exception):
            httpx.post(
                f"{base_url}/auth/logout",
                json={"refresh_token": creds["refresh_token"]},
                timeout=AUTH_TIMEOUT,
            )

    if CREDENTIALS_FILE.exists():
        CREDENTIALS_FILE.unlink()


def get_token() -> str:
    """Get a valid access token, auto-refreshing if expired."""
    creds = _load_credentials()
    if not creds:
        raise RuntimeError(f"Not logged in. Run: uv run scripts/{_ENTRY} login")

    # Check if token is still valid
    if time.time() < creds.get("expires_at", 0):
        return creds["access_token"]

    # Token expired, try refresh
    base_url = creds.get("api_url") or get_api_url()
    refresh = creds.get("refresh_token")
    if not refresh:
        raise RuntimeError(f"Session expired (no refresh token). Run: {_ENTRY} login")

    data = _refresh_token(base_url, refresh)
    _save_credentials(
        {
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
            "expires_at": time.time() + data["expires_in"] - 60,
            "api_url": base_url,
        }
    )
    return data["access_token"]
