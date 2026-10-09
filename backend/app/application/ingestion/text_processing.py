"""Normalização e divisão em trechos (ING-05, ING-06). Não altera números, datas ou valores."""

from __future__ import annotations

import hashlib
import re
import unicodedata

# 900 caracteres: medido em 28 perguntas com ouro literal, trechos de 900 recuperaram 24 delas contra 20 com 520
# (hipótese inicial de que o vetor ignorava a cauda além de 128 tokens não se confirmou na recuperação híbrida).
MAX_CHUNK_CHARS = 900
MIN_CHUNK_ALNUM = 6  # despachos curtos ("Defiro.", "Cite-se a ré.") precisam virar trecho
OVERLAP_CHARS = 100

_HYPHEN_BREAK = re.compile(r"(\w)-\n\s*([a-zà-ÿ])")
_SPACES = re.compile(r"[ \t ]+")
_BLANKS = re.compile(r"\n{3,}")


_CP1252_SPECIALS = "\u20ac\u201a\u0192\u201e\u2026\u2020\u2021\u02c6\u2030\u0160\u2039\u0152\u017d\u2018\u2019\u201c\u201d\u2022\u2013\u2014\u02dc\u2122\u0161\u203a\u0153\u017e\u0178"
_MOJIBAKE = re.compile(rf"([ÃÂ])([\u0080-\u00bf{_CP1252_SPECIALS}])")


def _repair_pair(m: re.Match[str]) -> str:
    lead, cont = m.group(1), m.group(2)
    try:
        byte2 = cont.encode("cp1252")[0] if ord(cont) > 0xFF or 0x80 <= ord(cont) <= 0x9F else ord(cont)
        return bytes([0xC3 if lead == "Ã" else 0xC2, byte2]).decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError, IndexError):
        return m.group(0)


def fix_mojibake(text: str) -> str:
    """Repara UTF-8 lido como Latin-1/CP1252 ("HonorÃ¡rios" -> "Honorários"), par a par, sem tocar em texto correto."""
    return _MOJIBAKE.sub(_repair_pair, text) if _MOJIBAKE.search(text) else text


def normalize_text(text: str) -> str:
    """Une hifenização de fim de linha e compacta espaços, preservando parágrafos."""
    t = unicodedata.normalize("NFC", fix_mojibake(text.replace("\r", "")))
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
    return [c for c in chunks if len(re.sub(r"\W", "", c)) >= MIN_CHUNK_ALNUM]
