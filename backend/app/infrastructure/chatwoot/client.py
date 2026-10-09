"""Cliente HTTP da API do Chatwoot com nova tentativa e recuo exponencial (CHW-06). Nunca registra tokens."""

from __future__ import annotations

import logging
import time
from typing import Any, Sequence

import httpx

log = logging.getLogger(__name__)

_TRANSIENT = {408, 425, 429, 500, 502, 503, 504}


class ChatwootError(RuntimeError):
    pass


class ChatwootClient:
    def __init__(
        self, base_url: str, account_id: int, *, bot_token: str = "", api_token: str = "", timeout_s: float = 10.0,
        retries: int = 3, backoff_s: float = 0.5, transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.account_id, self.retries, self.backoff_s = account_id, retries, backoff_s
        self._bot_token, self._api_token = bot_token, api_token
        self._http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout_s, transport=transport)
        self._teams: dict[str, int] | None = None

    # ----------------------------------------------------------------- base
    def _request(self, method: str, path: str, *, admin: bool = False, json: Any = None) -> Any:
        token = (self._api_token or self._bot_token) if admin else (self._bot_token or self._api_token)
        if not token:
            raise ChatwootError("token do Chatwoot não configurado")
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                r = self._http.request(method, path, json=json, headers={"api_access_token": token})
                if r.status_code in _TRANSIENT and attempt < self.retries:
                    last = ChatwootError(f"HTTP {r.status_code}")
                else:
                    if r.status_code >= 400:
                        raise ChatwootError(f"{method} {path.split('?')[0]} -> HTTP {r.status_code}")
                    return r.json() if r.content else {}
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = exc
                if attempt >= self.retries:
                    break
            time.sleep(self.backoff_s * (2**attempt))
        raise ChatwootError(f"{method} {path.split('?')[0]} falhou após {self.retries + 1} tentativas: {type(last).__name__}")

    def _acc(self, suffix: str) -> str:
        return f"/api/v1/accounts/{self.account_id}{suffix}"

    # --------------------------------------------------------- conversas
    def send_message(self, account_id: int, conversation_id: int, content: str) -> int | None:
        data = self._request(
            "POST", f"/api/v1/accounts/{account_id}/conversations/{conversation_id}/messages",
            json={"content": content, "message_type": "outgoing", "private": False},
        )
        return data.get("id")

    def get_labels(self, account_id: int, conversation_id: int) -> list[str]:
        data = self._request("GET", f"/api/v1/accounts/{account_id}/conversations/{conversation_id}/labels")
        return list(data.get("payload", []))

    def add_labels(self, account_id: int, conversation_id: int, labels: Sequence[str]) -> None:
        """A API substitui o conjunto de etiquetas; por isso lê, une e grava (não perde etiquetas existentes)."""
        current = self.get_labels(account_id, conversation_id)
        merged = list(dict.fromkeys([*current, *labels]))
        if merged != current:
            self._request("POST", f"/api/v1/accounts/{account_id}/conversations/{conversation_id}/labels", json={"labels": merged})

    def assign_team(self, account_id: int, conversation_id: int, team_name: str) -> bool:
        team_id = self.team_id(team_name)
        if team_id is None:
            return False
        self._request("POST", f"/api/v1/accounts/{account_id}/conversations/{conversation_id}/assignments", json={"team_id": team_id})
        return True

    def set_status(self, account_id: int, conversation_id: int, status: str) -> bool:
        self._request("POST", f"/api/v1/accounts/{account_id}/conversations/{conversation_id}/toggle_status", json={"status": status})
        return True

    # ------------------------------------------------------- administração
    def list_teams(self) -> list[dict]:
        return self._request("GET", self._acc("/teams"), admin=True)

    def team_id(self, name: str) -> int | None:
        if self._teams is None:
            self._teams = {t["name"].casefold(): t["id"] for t in self.list_teams()}
        return self._teams.get(name.casefold())

    def create_team(self, name: str, description: str = "") -> dict:
        self._teams = None
        return self._request("POST", self._acc("/teams"), admin=True, json={"name": name, "description": description, "allow_auto_assign": False})

    def list_labels(self) -> list[dict]:
        return self._request("GET", self._acc("/labels"), admin=True).get("payload", [])

    def create_label(self, title: str, description: str = "", color: str = "#1F93FF") -> dict:
        return self._request("POST", self._acc("/labels"), admin=True,
                             json={"title": title, "description": description, "color": color, "show_on_sidebar": True})

    def list_inboxes(self) -> list[dict]:
        return self._request("GET", self._acc("/inboxes"), admin=True).get("payload", [])

    def list_agent_bots(self) -> list[dict]:
        return self._request("GET", self._acc("/agent_bots"), admin=True)

    def create_agent_bot(self, name: str, outgoing_url: str, description: str = "") -> dict:
        return self._request("POST", self._acc("/agent_bots"), admin=True,
                             json={"name": name, "description": description, "outgoing_url": outgoing_url})

    def set_inbox_agent_bot(self, inbox_id: int, agent_bot_id: int) -> None:
        self._request("POST", self._acc(f"/inboxes/{inbox_id}/set_agent_bot"), admin=True, json={"agent_bot": agent_bot_id})
