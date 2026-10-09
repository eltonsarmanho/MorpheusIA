"""Normalização e divisão em trechos (ING-05, ING-06). Não altera números, datas ou valores."""

from __future__ import annotations

import hashlib
import re
import unicodedata

MAX_CHUNK_CHARS = 900
OVERLAP_CHARS = 100

_HYPHEN_BREAK = re.compile(r"(\w)-\n\s*([a-zà-ÿ])")
_SPACES = re.compile(r"[ \t ]+")
_BLANKS = re.compile(r"\n{3,}")


def normalize_text(text: str) -> str:
    """Une hifenização de fim de linha e compacta espaços, preservando parágrafos."""
    t = unicodedata.normalize("NFC", text.replace("\r", ""))
    t = t.replace("­", "")
    t = _HYPHEN_BREAK.sub(r"\1\2", t)
    lines = [_SPACES.sub(" ", ln).strip() for ln in t.split("\n")]
    t = "\n".join(lines)
    return _BLANKS.sub("\n\n", t).strip()


def content_hash(text: str) -> str:
    folded = re.sub(r"\s+", " ", text).strip().lower()
    return hashlib.sha256(folded.encode("utf-8")).hexdigest()[:32]


def _paragraphs(text: str) -> list[str]:
    paras = [re.sub(r"\s*\n\s*", " ", p).strip() for p in re.split(r"\n\s*\n", text)]
    return [p for p in paras if p]


def _split_long(sentence: str, limit: int) -> list[str]:
    parts, cur = [], ""
    for word in sentence.split(" "):
        if len(cur) + len(word) + 1 > limit and cur:
            parts.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    if cur:
        parts.append(cur)
    return parts


def chunk_text(text: str, max_chars: int = MAX_CHUNK_CHARS, overlap: int = OVERLAP_CHARS) -> list[str]:
    """Agrupa parágrafos até `max_chars`; trechos consecutivos compartilham uma sobreposição curta."""
    pieces: list[str] = []
    for para in _paragraphs(text):
        pieces.extend(_split_long(para, max_chars) if len(para) > max_chars else [para])
    chunks: list[str] = []
    cur = ""
    for piece in pieces:
        if cur and len(cur) + len(piece) + 1 > max_chars:
            chunks.append(cur)
            tail = cur[-overlap:].split(" ", 1)[-1] if overlap else ""
            cur = f"{tail}\n{piece}".strip() if tail and len(tail) + len(piece) + 1 <= max_chars else piece
        else:
            cur = f"{cur}\n{piece}".strip()
    if cur:
        chunks.append(cur)
    return [c for c in chunks if len(re.sub(r"\W", "", c)) >= 20]
