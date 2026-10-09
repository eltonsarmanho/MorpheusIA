"""Registro de fontes autorizadas (COL-01)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import yaml

from app.domain.models import KnowledgeDomain


class CollectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Seed:
    url: str
    topic: str
    norm: str = ""


@dataclass(frozen=True)
class Source:
    id: str
    domain: KnowledgeDomain
    issuing_body: str
    hosts: tuple[str, ...]
    seeds: tuple[Seed, ...] = ()


@dataclass(frozen=True)
class CollectionSettings:
    user_agent: str = "piloto-tjpa-coletor/0.1"
    timeout_s: float = 30.0
    max_bytes: int = 8_000_000
    stale_after_days: int = 180


@dataclass
class SourceRegistry:
    sources: list[Source] = field(default_factory=list)
    settings: CollectionSettings = field(default_factory=CollectionSettings)

    @classmethod
    def from_file(cls, path: Path) -> "SourceRegistry":
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        s = raw.get("settings", {})
        settings = CollectionSettings(
            user_agent=s.get("user_agent", CollectionSettings.user_agent), timeout_s=float(s.get("timeout_s", 30)),
            max_bytes=int(s.get("max_bytes", 8_000_000)), stale_after_days=int(s.get("stale_after_days", 180)),
        )
        sources = [
            Source(
                id=x["id"], domain=KnowledgeDomain(x["domain"]), issuing_body=x["issuing_body"], hosts=tuple(x["hosts"]),
                seeds=tuple(Seed(d["url"], d.get("topic", ""), d.get("norm", "")) for d in x.get("seeds", [])),
            )
            for x in raw.get("sources", [])
        ]
        return cls(sources, settings)

    def resolve(self, url: str, domain: KnowledgeDomain) -> Source:
        """Devolve a fonte que autoriza a URL no domínio pedido ou levanta CollectionError."""
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise CollectionError("somente URLs https sem credenciais são aceitas")
        host = parsed.hostname.lower()
        for src in self.sources:
            if src.domain is domain and host in src.hosts:
                return src
        raise CollectionError(f"fonte não autorizada para o domínio {domain.value}: {host}")
