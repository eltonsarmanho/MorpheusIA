"""Carga dos contatos da Ouvidoria da Mulher (`backend/config/mulher.yaml`). Campo inválido é descartado, nunca corrigido."""

from __future__ import annotations

import logging
import re
from datetime import time
from pathlib import Path

import yaml

from app.domain.triage_mulher import OuvidoriaMulher

log = logging.getLogger(__name__)
_EMAIL = re.compile(r"^[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+$")
_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def _hhmm(raw, default: time) -> time:
    m = _HHMM.match(str(raw or "").strip())
    return time(int(m.group(1)), int(m.group(2))) if m else default


def load_ouvidoria_mulher(path: Path | str | None) -> OuvidoriaMulher:
    if not path or not Path(path).exists():
        return OuvidoriaMulher()
    data = (yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}).get("ouvidoria_mulher") or {}
    email = str(data.get("email") or "").strip()
    if email and not _EMAIL.match(email):
        log.warning("contatos da Ouvidoria da Mulher: e-mail inválido descartado")
        email = ""
    phone = str(data.get("telefone") or "").strip()
    if phone and len(re.sub(r"\D", "", phone)) not in (10, 11):
        log.warning("contatos da Ouvidoria da Mulher: telefone inválido descartado")
        phone = ""
    d = OuvidoriaMulher()
    opens, closes = _hhmm(data.get("abre"), d.opens), _hhmm(data.get("fecha"), d.closes)
    if opens >= closes:
        opens, closes = d.opens, d.closes
    return OuvidoriaMulher(phone=phone or None, email=email or None, opens=opens, closes=closes,
                           source=str(data.get("fonte") or ""), verified_at=str(data.get("verificado_em") or ""))
