"""Carga do cadastro de unidades (`backend/config/units.yaml`). Campo inválido é descartado e anotado, nunca corrigido."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from app.domain.units import InstitutionalContacts, Unit, UnitDirectory, valid_br_phone, valid_official_url

log = logging.getLogger(__name__)


def load_unit_directory(path: Path | str | None) -> UnitDirectory:
    """Arquivo ausente ou vazio = cadastro vazio; o fluxo declara a falta de dados em vez de falhar."""
    if not path or not Path(path).exists():
        return UnitDirectory()
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    rejected: list[str] = []

    def checked(owner: str, name: str, raw, validator):
        value = validator(raw)
        if raw and value is None:
            rejected.append(f"{owner}.{name}")
        return value

    inst = data.get("institucional") or {}
    contacts = InstitutionalContacts(
        balcao_general_url=checked("institucional", "balcao_virtual_url_geral", inst.get("balcao_virtual_url_geral"), valid_official_url),
        balcao_general_source=str(inst.get("balcao_virtual_fonte") or ""),
        telejudiciario=(str(inst["telejudiciario"]).strip() or None) if inst.get("telejudiciario") else None,
        telejudiciario_source=str(inst.get("telejudiciario_fonte") or ""),
    )
    units: list[Unit] = []
    for raw in data.get("unidades") or []:
        uid, name = str(raw.get("id") or "").strip(), str(raw.get("nome") or "").strip()
        if not uid or not name or raw.get("valido") is False:
            continue
        units.append(Unit(
            id=uid, name=name, city=str(raw.get("cidade") or "").strip(), aliases=tuple(str(a) for a in raw.get("aliases") or ()),
            address=(str(raw["endereco"]).strip() or None) if raw.get("endereco") else None,
            phone=checked(uid, "telefone", raw.get("telefone"), valid_br_phone),
            whatsapp=checked(uid, "whatsapp", raw.get("whatsapp"), valid_br_phone),
            balcao_url=checked(uid, "balcao_virtual_url", raw.get("balcao_virtual_url"), valid_official_url),
            hours=(str(raw["horario"]).strip() or None) if raw.get("horario") else None,
            timezone=raw.get("fuso"), source=str(raw.get("fonte") or ""), verified_at=str(raw.get("verificado_em") or ""),
        ))
    if rejected:
        log.warning("cadastro de unidades: %d campo(s) inválido(s) descartado(s)", len(rejected))
    return UnitDirectory(units, contacts, rejected)
