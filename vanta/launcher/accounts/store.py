"""Local test accounts persisted under the Vanta data directory."""

from __future__ import annotations

import copy
import re
import uuid
from pathlib import Path
from typing import Any

from vanta.shared.utilities.jsonio import read_json, write_json

LOCAL_LABEL = "LOCAL TEST ACCOUNT"
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")
LOCAL_COMMENT = (
    "Not valid for online-mode servers. "
    "This record must not be used as an online-mode authentication bypass."
)


class AccountStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.path = directory / "accounts.json"
        self.directory.mkdir(parents=True, exist_ok=True)
        if not self.path.is_file():
            write_json(self.path, {"accounts": []})

    def list(self) -> list[dict[str, Any]]:
        data = read_json(self.path, {"accounts": []})
        accounts = data.get("accounts", []) if isinstance(data, dict) else []
        return [copy.deepcopy(item) for item in accounts if isinstance(item, dict)]

    def get(self, account_id: str | None) -> dict[str, Any] | None:
        if not account_id:
            return None
        for account in self.list():
            if account.get("id") == account_id:
                return account
        return None

    def create_local(self, username: str) -> dict[str, Any]:
        cleaned = (username or "").strip()
        if not USERNAME_RE.fullmatch(cleaned):
            raise ValueError(
                "Username must be 1–16 characters and use only letters, numbers, and underscore."
            )
        account_uuid = str(uuid.uuid4())
        account = {
            "id": "local-" + account_uuid.replace("-", ""),
            "kind": "local",
            "label": LOCAL_LABEL,
            "username": cleaned,
            "uuid": account_uuid,
            "mode": "local",
            "onlineModeValid": False,
            "comment": LOCAL_COMMENT,
        }
        accounts = self.list()
        accounts.append(account)
        self._write(accounts)
        return copy.deepcopy(account)

    def save_local_skin(
        self,
        account_id: str,
        *,
        skin_id: str,
        model: str,
        relative_file: str,
        source: str,
        note: str,
    ) -> dict[str, Any]:
        """Attach a locally saved skin. Never records a Microsoft upload."""
        if model not in {"steve", "alex"}:
            raise ValueError("Skin model must be steve or alex.")
        accounts = self.list()
        for account in accounts:
            if account.get("id") != account_id:
                continue
            if (
                account.get("label") != LOCAL_LABEL
                or account.get("mode") != "local"
                or account.get("kind") != "local"
            ):
                raise ValueError("Skins can only be saved on a LOCAL TEST ACCOUNT.")
            account["skinId"] = skin_id
            account["skinModel"] = model
            account["skinFile"] = relative_file
            account["skinSource"] = source
            account["skinNote"] = note
            account["skinUploadedToMicrosoft"] = False
            self._write(accounts)
            return copy.deepcopy(self.get(account_id) or account)
        raise ValueError("No local account selected. Create a LOCAL TEST ACCOUNT before saving a skin.")

    def _write(self, accounts: list[dict[str, Any]]) -> None:
        for account in accounts:
            for key in ("token", "tokens", "accessToken", "refreshToken", "clientSecret"):
                account.pop(key, None)
        write_json(self.path, {"accounts": accounts})
