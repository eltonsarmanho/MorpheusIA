"""Extração de conteúdo de páginas HTML oficiais, com data, vigência e referência normativa."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime

from bs4 import BeautifulSoup

from app.application.ingestion.text_processing import normalize_text
from app.domain.models import DESCONHECIDO

_MONTHS = {m: i for i, m in enumerate(
    ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"], 1)}
_DATE_LONG = re.compile(r"(\d{1,2})º?\s+de\s+(janeiro|fevereiro|março|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)\s+de\s+(\d{4})", re.I)
_DATE_BR = re.compile(r"(\d{2})/(\d{2})/(\d{4})")
_PUBLISHED = re.compile(r"(?i)(?:publicad[oa]|publicação)\s*(?:em|:)?\s*(\d{2}/\d{2}/\d{4})")
_STATUS = re.compile(r"(?i)\bSitua[cç][aã]o\s+(Revogad[oa]|Alterad[oa]|Vigente|Em vigor|Suspens[oa])")
_REVOKED_HEADER = re.compile(r"(?i)\(\s*(?:revogad[oa]|vig[eê]ncia encerrada)[^)]{0,120}\)")
_ARTICLE = re.compile(r"(?m)^\s*(Art\.\s*\d+[ºo°]?(?:-[A-Z])?)")


@dataclass
class ExtractedPage:
    title: str
    text: str
    published_at: str = DESCONHECIDO
    validity_flag: str = DESCONHECIDO  # vigente | alterado | revogado | desconhecido
    quality: dict[str, object] = field(default_factory=dict)


def _iso(day: str, month: str, year: str) -> str:
    try:
        return datetime(int(year), int(month), int(day)).date().isoformat()
    except ValueError:
        return DESCONHECIDO


def find_date(text: str) -> str:
    m = _PUBLISHED.search(text)
    if m:
        d = _DATE_BR.match(m.group(1))
        return _iso(d.group(1), d.group(2), d.group(3)) if d else DESCONHECIDO
    return DESCONHECIDO


def extract_html(body: bytes, *, kind: str = "pagina") -> ExtractedPage:
    soup = BeautifulSoup(body, "lxml")
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "form", "svg", "iframe", "button", "select"]):
        tag.decompose()
    title = (soup.title.get_text(" ", strip=True) if soup.title else "") or (soup.h1.get_text(" ", strip=True) if soup.h1 else "")
    root = soup.find("main") or soup.find("article") or soup.body or soup
    # separa células e itens para não colar texto de tabelas
    for br in root.find_all("br"):
        br.replace_with("\n")
    for el in root.find_all(["p", "div", "li", "tr", "h1", "h2", "h3", "h4", "table"]):
        el.append("\n")
    for el in root.find_all(["td", "th"]):
        el.append(" | ")
    raw = html.unescape(root.get_text(" "))
    text = normalize_text(re.sub(r"[ \t]*\n[ \t]*", "\n", raw))
    text = re.sub(r"(?m)^\s*\|\s*$", "", text)
    page = ExtractedPage(title=re.sub(r"\s+", " ", title).strip() or DESCONHECIDO, text=text)
    page.published_at = find_date(text)
    st = _STATUS.search(text)
    head = text[:1200]
    if _REVOKED_HEADER.search(head) or (st and st.group(1).lower().startswith("revog")):
        page.validity_flag = "revogado"
    elif st:
        page.validity_flag = "alterado" if st.group(1).lower().startswith("alter") else "vigente"
    chars = len(text)
    page.quality = {"chars": chars, "low_text": chars < 500, "has_title": page.title != DESCONHECIDO}
    return page


def split_articles(text: str) -> list[tuple[str, str]]:
    """Divide uma norma por artigo, devolvendo (rótulo do dispositivo, texto original)."""
    matches = list(_ARTICLE.finditer(text))
    if len(matches) < 3:
        return [("", text)]
    parts: list[tuple[str, str]] = []
    if matches[0].start() > 0:
        parts.append(("Preâmbulo", text[: matches[0].start()].strip()))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        parts.append((re.sub(r"\s+", " ", m.group(1)), text[m.start() : end].strip()))
    return [(a, b) for a, b in parts if b]
