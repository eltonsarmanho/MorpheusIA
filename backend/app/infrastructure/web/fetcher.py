"""Busca HTTP respeitosa: robots.txt, tamanho máximo, sem credenciais e sem contornar controles (COL-07)."""

from __future__ import annotations

import logging
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from app.application.collection.sources import CollectionError, CollectionSettings

log = logging.getLogger(__name__)


@dataclass
class FetchResult:
    url: str
    status: int
    content_type: str
    body: bytes


class HttpFetcher:
    def __init__(self, settings: CollectionSettings, transport: httpx.BaseTransport | None = None) -> None:
        self.settings = settings
        self._http = httpx.Client(
            timeout=settings.timeout_s, follow_redirects=False, headers={"User-Agent": settings.user_agent}, transport=transport
        )
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def _robots_for(self, url: str) -> urllib.robotparser.RobotFileParser | None:
        p = urlparse(url)
        origin = f"{p.scheme}://{p.netloc}"
        if origin in self._robots:
            return self._robots[origin]
        rp = urllib.robotparser.RobotFileParser()
        parser: urllib.robotparser.RobotFileParser | None
        try:
            r = self._http.get(f"{origin}/robots.txt")
            if r.status_code == 200:
                rp.parse(r.text.splitlines())
                parser = rp
            elif 400 <= r.status_code < 500:
                parser = None  # sem robots.txt: sem restrições declaradas (RFC 9309)
            else:
                raise CollectionError(f"robots.txt de {p.netloc} indisponível (HTTP {r.status_code}); coleta suspensa")
        except httpx.HTTPError as exc:
            raise CollectionError(f"robots.txt de {p.netloc} inacessível ({type(exc).__name__}); coleta suspensa") from exc
        self._robots[origin] = parser
        return parser

    def fetch(self, url: str) -> FetchResult:
        origin_host = urlparse(url).hostname
        current = url
        for _hop in range(4):
            rp = self._robots_for(current)
            if rp is not None and not rp.can_fetch(self.settings.user_agent, current):
                raise CollectionError(f"robots.txt não permite a coleta de {current}")
            try:
                with self._http.stream("GET", current) as r:
                    if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                        nxt = str(httpx.URL(current).join(r.headers["location"]))
                        # a requisição ao host não autorizado nem chega a ser feita
                        if urlparse(nxt).hostname != origin_host or urlparse(nxt).scheme != "https":
                            raise CollectionError(f"redirecionamento para outro host recusado: {urlparse(nxt).hostname}")
                        current = nxt
                        continue
                    chunks, size = [], 0
                    for part in r.iter_bytes():
                        size += len(part)
                        if size > self.settings.max_bytes:
                            raise CollectionError(f"conteúdo excede {self.settings.max_bytes} bytes")
                        chunks.append(part)
                    return FetchResult(current, r.status_code, r.headers.get("content-type", ""), b"".join(chunks))
            except httpx.HTTPError as exc:
                raise CollectionError(f"falha de rede ao coletar: {type(exc).__name__}") from exc
        raise CollectionError("redirecionamentos demais")
