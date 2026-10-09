"""Cliente HTTP do Chatwoot (nova tentativa, etiquetas sem perda) e script de preparação idempotente (CHW-06, CHW-08)."""
import httpx
import pytest

from app.infrastructure.chatwoot.client import ChatwootClient, ChatwootError
from app.interfaces import chatwoot_setup as setup


class FakeChatwoot:
    """Servidor Chatwoot mínimo em memória, via MockTransport."""

    def __init__(self):
        self.teams = [{"id": 1, "name": "informações processuais"}]
        self.labels: list[dict] = []
        self.conv_labels = ["antiga"]
        self.calls: list[tuple[str, str]] = []
        self.tokens: dict[str, str] = {}
        self.flaky = 0
        self.bots: list[dict] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path, m = req.url.path, req.method
        self.calls.append((m, path))
        token = req.headers.get("api_access_token")
        assert token, "token ausente"
        self.tokens[path] = token
        if token == "b" and (path.endswith("/labels") or path.endswith("/teams") or path.endswith("/agent_bots") or path.endswith("/inboxes")):
            return httpx.Response(401, json={"error": "Access to this endpoint is not authorized for bots"})  # comportamento real do Chatwoot
        if self.flaky:
            self.flaky -= 1
            return httpx.Response(503)
        import json

        body = json.loads(req.content) if req.content else {}
        if path.endswith("/teams") and m == "GET":
            return httpx.Response(200, json=self.teams)
        if path.endswith("/teams") and m == "POST":
            t = {"id": len(self.teams) + 1, "name": body["name"].lower()}
            self.teams.append(t)
            return httpx.Response(200, json=t)
        if path.endswith("/labels") and "conversations" not in path:
            if m == "GET":
                return httpx.Response(200, json={"payload": self.labels})
            self.labels.append({"title": body["title"]})
            return httpx.Response(200, json=body)
        if path.endswith("/conversations/9/labels"):
            if m == "GET":
                return httpx.Response(200, json={"payload": self.conv_labels})
            self.conv_labels = body["labels"]
            return httpx.Response(200, json={"payload": self.conv_labels})
        if path.endswith("/conversations/9/assignments"):
            return httpx.Response(200, json={"id": body["team_id"]})
        if path.endswith("/conversations/9/toggle_status"):
            return httpx.Response(200, json={"payload": {"current_status": body["status"]}})
        if path.endswith("/conversations/9/messages"):
            return httpx.Response(200, json={"id": 77})
        if path.endswith("/agent_bots"):
            if m == "GET":
                return httpx.Response(200, json=self.bots)
            bot = {"id": 5, "name": body["name"], "access_token": "TOKEN-DO-BOT", "outgoing_url": body["outgoing_url"]}
            self.bots.append(bot)
            return httpx.Response(200, json=bot)
        if path.endswith("/agent_bots/5") and m == "PATCH":
            self.bots[0]["outgoing_url"] = body["outgoing_url"]
            return httpx.Response(200, json=self.bots[0])
        if path.endswith("/inboxes"):
            return httpx.Response(200, json={"payload": [{"id": 1, "name": "WhatsApp TJPA", "channel_type": "Channel::Whatsapp"}]})
        if path.endswith("/set_agent_bot"):
            return httpx.Response(200, json={})
        return httpx.Response(404)


@pytest.fixture
def fake():
    return FakeChatwoot()


@pytest.fixture
def client(fake):
    return ChatwootClient("https://cw.example", 1, bot_token="b", api_token="a", backoff_s=0, transport=httpx.MockTransport(fake))


def test_etiquetas_sao_somadas_nao_substituidas(client, fake):  # a API do Chatwoot sobrescreve o conjunto
    client.add_labels(1, 9, ["humano", "ia_rag"])
    assert fake.conv_labels == ["antiga", "humano", "ia_rag"]
    client.add_labels(1, 9, ["humano"])
    assert fake.conv_labels == ["antiga", "humano", "ia_rag"]


def test_atribuicao_de_equipe_compara_nome_sem_maiusculas(client):
    assert client.assign_team(1, 9, "Informações Processuais") is True
    assert client.assign_team(1, 9, "Equipe que não existe") is False


def test_erro_transitorio_tem_nova_tentativa_e_erro_definitivo_nao(client, fake):  # CHW-06
    fake.flaky = 2
    assert client.send_message(1, 9, "oi") == 77
    assert [c for c in fake.calls if c[1].endswith("/messages")].__len__() == 3
    with pytest.raises(ChatwootError):
        client._request("GET", "/api/v1/accounts/1/inexistente")


def test_falhas_seguidas_esgotam_as_tentativas(fake):
    fake.flaky = 99
    c = ChatwootClient("https://cw.example", 1, bot_token="b", retries=2, backoff_s=0, transport=httpx.MockTransport(fake))
    with pytest.raises(ChatwootError, match="HTTP 503"):
        c.send_message(1, 9, "oi")
    assert len(fake.calls) == 3  # 1 tentativa + 2 novas


def test_sem_token_nao_chama_a_rede(fake):
    c = ChatwootClient("https://cw.example", 1, transport=httpx.MockTransport(fake))
    with pytest.raises(ChatwootError, match="token"):
        c.send_message(1, 9, "oi")
    assert fake.calls == []


def test_setup_cria_so_o_que_falta_e_e_idempotente(client, fake):  # CHW-08
    p = setup.plan(client, None, None)
    assert p["teams_to_create"] == ["Triagem e Orquestração", "Informações Institucionais", "Informações Jurídico-Institucionais", "Atendimento Humano Geral"]
    assert len(p["labels_to_create"]) == 6
    out = setup.apply(client, None, None)
    assert len(out["created_teams"]) == 4 and len(out["created_labels"]) == 6
    again = setup.apply(client, None, None)
    assert again["created_teams"] == [] and again["created_labels"] == []
    assert len(fake.teams) == 5 and len(fake.labels) == 6


def test_setup_dry_run_nao_escreve(client, fake):
    setup.plan(client, "https://x/webhook?token=s", 1)
    assert not [c for c in fake.calls if c[0] == "POST"]


def test_setup_cria_agent_bot_uma_vez_e_liga_a_inbox(client, fake):
    first = setup.apply(client, "https://x/webhook?token=s", 1)
    assert first["agent_bot"]["created"] is True and first["inbox_bound"] == 1 and first["bot_access_token"] == "TOKEN-DO-BOT"
    second = setup.apply(client, "https://x/webhook?token=s", 1)
    assert second["agent_bot"]["created"] is False and len(fake.bots) == 1


def test_setup_atualiza_a_url_do_agent_bot_quando_ela_muda(client, fake):
    setup.apply(client, "https://publico/webhook?token=s", 1)
    out = setup.apply(client, "http://tjpa_backend:8300/webhooks/chatwoot?token=s", 1)
    assert out["agent_bot"]["url_updated"] is True and fake.bots[0]["outgoing_url"].startswith("http://tjpa_backend")


def test_etiquetas_e_equipes_usam_o_token_de_usuario_e_mensagens_usam_o_do_bot(client, fake):
    client.add_labels(1, 9, ["humano"])
    client.assign_team(1, 9, "Informações Processuais")
    client.send_message(1, 9, "oi")
    assert fake.tokens["/api/v1/accounts/1/conversations/9/labels"] == "a"
    assert fake.tokens["/api/v1/accounts/1/teams"] == "a"
    assert fake.tokens["/api/v1/accounts/1/conversations/9/messages"] == "b"
    assert fake.tokens["/api/v1/accounts/1/conversations/9/assignments"] == "b"
