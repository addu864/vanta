"""Microsoft sign-in structure for Vanta 0.1.

Public endpoint names are recorded so a later milestone can start a real
device-code flow. This milestone does not ship a client id, does not call
those endpoints, does not complete login, and does not store tokens.
"""

from __future__ import annotations

# Public Microsoft endpoints. No client secret belongs beside them.
DEVICE_CODE_ENDPOINT = "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
TOKEN_ENDPOINT = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"

# Intentionally unset. Embedding a client id or secret is out of scope for 0.1.
CLIENT_ID: str | None = None

NOT_COMPLETED = (
    "Microsoft sign-in is not completed in 0.1 (no client id / interactive login)."
)


def begin_microsoft_sign_in(client_id: str | None = None, client_secret: str | None = None) -> dict[str, object]:
    """Return an honest failure. Never a session, never a token."""
    if client_secret:
        return {
            "ok": False,
            "completed": False,
            "authenticated": False,
            "status": "not_completed",
            "error": "Vanta does not accept a Microsoft client secret. " + NOT_COMPLETED,
            "tokensStored": False,
        }
    # A caller-supplied client id is still not enough: 0.1 has no interactive
    # login step and must not pretend the device-code exchange finished.
    _ = client_id or CLIENT_ID
    return {
        "ok": False,
        "completed": False,
        "authenticated": False,
        "status": "not_completed",
        "error": NOT_COMPLETED,
        "tokensStored": False,
    }
