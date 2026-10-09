"""Leitura estrutural do PDF de processo exportado pelo PJe.

Tudo aqui é função pura sobre texto. O formato observado no acervo:
- capa com dados do processo e a tabela "Documentos" (Id., Data, Documento, Tipo);
- um rodapé em cada página: "Assinado eletronicamente por ... Num. <id> - Pág. <n>".
Metadados que não puderem ser lidos ficam como `desconhecido` (ING-07).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from app.domain.models import DESCONHECIDO

_FOOTER = re.compile(
    r"Assinado eletronicamente por:(?P<sig>.*?)Num\.\s*(?:\d{2}:\d{2}:\d{2}\s*)?(?P<id>\d{6,})\s*-\s*P[áa]g\.\s*(?P<pag>\d+)"
    r"(?:\s*https?://\S+)?(?:\s*N[úu]mero do documento:\s*\d+)?",
    re.S,
)
_SIGNER = re.compile(r"(?P<name>[^\n]*?)\s+-\s+(?P<dt>\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2})")
_ROW = re.compile(r"^\s*(?P<id>\d{6,})\s+(?P<date>\d{2}/\d{2}/\d{4})\s+(?P<rest>\S.*?)\s*$")
_TIME_CONT = re.compile(r"^\s*(?P<time>\d{2}:\d{2})(?:\s+(?P<cont>\S.*?))?\s*$")
_SPLIT_COLS = re.compile(r"\s{2,}")
_ROLE = re.compile(r"^(?P<name>.+?)\s*\((?P<role>[A-ZÀ-Ý][A-ZÀ-Ý /\-]+)\)\s*$")

# Tipos conhecidos do PJe; usados apenas quando a coluna "Tipo" foi cortada na tabela.
_KNOWN_TYPES = frozenset(
    {
        "decisão", "despacho", "sentença", "certidão", "petição", "petição inicial", "procuração",
        "mandado", "intimação", "acórdão", "ofício", "edital", "voto", "ementa", "relatório",
    }
)


@dataclass(frozen=True)
class Footer:
    doc_id: str
    pje_page: int
    signer: str
    signed_at: str  # ISO 8601 ou "desconhecido"


@dataclass
class CoverRow:
    doc_id: str
    date: str  # ISO (YYYY-MM-DD)
    time: str
    name: str
    doc_type: str
    type_source: str  # "coluna_tipo" | "nome_do_documento" | "desconhecido"


@dataclass
class CoverInfo:
    tribunal: str = DESCONHECIDO
    number: str = DESCONHECIDO
    process_class: str = DESCONHECIDO
    court_unit: str = DESCONHECIDO
    distribution_date: str = DESCONHECIDO
    case_value: str = DESCONHECIDO
    subjects: str = DESCONHECIDO
    secrecy: str = DESCONHECIDO  # "SIM" | "NAO" | desconhecido
    free_justice: str = DESCONHECIDO
    exported_at: str = DESCONHECIDO
    parties: list[dict[str, str]] = field(default_factory=list)
    rows: list[CoverRow] = field(default_factory=list)


def _iso_date(value: str) -> str:
    try:
        return datetime.strptime(value.strip(), "%d/%m/%Y").date().isoformat()
    except ValueError:
        return DESCONHECIDO


def _iso_datetime(value: str) -> str:
    try:
        return datetime.strptime(value.strip(), "%d/%m/%Y %H:%M:%S").isoformat(timespec="seconds")
    except ValueError:
        return DESCONHECIDO


def parse_footer(page_text: str) -> Footer | None:
    """Devolve o último rodapé da página (o mais externo quando há PDFs aninhados)."""
    matches = list(_FOOTER.finditer(page_text))
    if not matches:
        return None
    m = matches[-1]
    signer, signed_at = DESCONHECIDO, DESCONHECIDO
    s = _SIGNER.search(m.group("sig"))
    if s:
        signer = re.sub(r"\s+", " ", s.group("name")).strip(" ,") or DESCONHECIDO
        signed_at = _iso_datetime(s.group("dt"))
    return Footer(doc_id=m.group("id"), pje_page=int(m.group("pag")), signer=signer, signed_at=signed_at)


def strip_footers(page_text: str) -> str:
    return _FOOTER.sub("", page_text)


def _field(text: str, label: str) -> str:
    m = re.search(rf"^{label}\s*:\s*(.+)$", text, re.M)
    return m.group(1).strip() if m else DESCONHECIDO


def _parse_parties(cover_text: str) -> list[dict[str, str]]:
    start = cover_text.find("Partes")
    end = cover_text.find("Documentos", start if start >= 0 else 0)
    if start < 0:
        return []
    block = cover_text[start:end if end > 0 else None].splitlines()[1:]
    parties: list[dict[str, str]] = []
    for line in block:
        for col in _SPLIT_COLS.split(line.strip()):
            m = _ROLE.match(col.strip())
            if m:
                parties.append({"nome": m.group("name").strip(), "papel": m.group("role").strip()})
    return parties


def _parse_rows(cover_text: str) -> list[CoverRow]:
    rows: list[CoverRow] = []
    current: dict[str, str] | None = None

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        name = re.sub(r"\s+", " ", current["name"]).strip()
        dtype, source = current["type"].strip(), "coluna_tipo"
        if not dtype:
            if name.lower() in _KNOWN_TYPES:
                dtype, source = name, "nome_do_documento"
            else:
                dtype, source = DESCONHECIDO, DESCONHECIDO
        rows.append(CoverRow(current["id"], _iso_date(current["date"]), current.get("time", ""), name, dtype, source))
        current = None

    for line in cover_text.splitlines():
        m = _ROW.match(line)
        if m:
            flush()
            cols = _SPLIT_COLS.split(m.group("rest"))
            current = {
                "id": m.group("id"),
                "date": m.group("date"),
                "name": cols[0],
                "type": cols[1] if len(cols) > 1 else "",
            }
            continue
        if current is not None:
            t = _TIME_CONT.match(line)
            if t:
                current["time"] = t.group("time")
                if t.group("cont"):
                    current["name"] += " " + t.group("cont")
    flush()
    return rows


def parse_cover(cover_text: str) -> CoverInfo:
    info = CoverInfo()
    first = next((ln.strip() for ln in cover_text.splitlines() if ln.strip()), "")
    if first.lower().startswith("tribunal"):
        info.tribunal = first
    info.number = _field(cover_text, "Número")
    info.process_class = _field(cover_text, "Classe")
    info.court_unit = _field(cover_text, "Órgão julgador")
    dist = re.search(r"Última distribuição\s*:\s*(\d{2}/\d{2}/\d{4})", cover_text)
    info.distribution_date = _iso_date(dist.group(1)) if dist else DESCONHECIDO
    info.case_value = _field(cover_text, "Valor da causa")
    subj = re.search(r"^Assuntos\s*:\s*(.+?)(?=^\s*Segredo de justi)", cover_text, re.M | re.S)
    if subj:
        info.subjects = re.sub(r"\s+", " ", subj.group(1)).strip()
    sec = re.search(r"Segredo de justi[cç]a\?\s*(SIM|N[ÃA]O)", cover_text, re.I)
    if sec:
        info.secrecy = "NAO" if sec.group(1).upper().startswith("N") else "SIM"
    fj = re.search(r"Justi[cç]a gratuita\?\s*(SIM|N[ÃA]O)", cover_text, re.I)
    if fj:
        info.free_justice = "NAO" if fj.group(1).upper().startswith("N") else "SIM"
    exp = re.search(r"^\s*(\d{2}/\d{2}/\d{4})\s*$", cover_text, re.M)
    if exp:
        info.exported_at = _iso_date(exp.group(1))
    info.parties = _parse_parties(cover_text)
    info.rows = _parse_rows(cover_text)
    return info


@dataclass
class PjePage:
    pdf_page: int  # 1-based, no PDF
    footer: Footer | None
    raw_text: str
    body: str  # sem rodapé
    inherited_doc_id: str | None = None  # página sem rodapé legível, herdada da anterior

    @property
    def doc_id(self) -> str | None:
        return self.footer.doc_id if self.footer else self.inherited_doc_id

    @property
    def pje_page(self) -> int | None:
        return self.footer.pje_page if self.footer else None


@dataclass
class DocumentSegment:
    pje_doc_id: str  # "capa" para as páginas iniciais sem rodapé
    pdf_pages: list[int]
    name: str = DESCONHECIDO
    doc_type: str = DESCONHECIDO
    type_source: str = DESCONHECIDO
    doc_date: str = DESCONHECIDO
    doc_date_source: str = DESCONHECIDO
    signer: str = DESCONHECIDO
    signed_at: str = DESCONHECIDO
    in_cover_table: bool = False


def split_pages(full_text: str) -> list[str]:
    pages = full_text.split("\f")
    if pages and not pages[-1].strip():
        pages = pages[:-1]
    return pages


def build_pages(texts: list[str]) -> list[PjePage]:
    pages: list[PjePage] = []
    last_doc: str | None = None
    for i, raw in enumerate(texts, start=1):
        footer = parse_footer(raw)
        page = PjePage(i, footer, raw, strip_footers(raw))
        if footer:
            last_doc = footer.doc_id
        elif last_doc is not None:
            page.inherited_doc_id = last_doc
        pages.append(page)
    return pages


def segment_documents(pages: list[PjePage], cover: CoverInfo) -> list[DocumentSegment]:
    """Agrupa páginas por documento PJe e junta metadados da tabela da capa."""
    rows = {r.doc_id: r for r in cover.rows}
    order: list[str] = []
    segments: dict[str, DocumentSegment] = {}
    for page in pages:
        doc_id = page.doc_id or "capa"
        seg = segments.get(doc_id)
        if seg is None:
            seg = DocumentSegment(pje_doc_id=doc_id, pdf_pages=[])
            segments[doc_id] = seg
            order.append(doc_id)
        seg.pdf_pages.append(page.pdf_page)
        if page.footer and seg.signer == DESCONHECIDO:
            seg.signer, seg.signed_at = page.footer.signer, page.footer.signed_at
    for doc_id in order:
        seg = segments[doc_id]
        if doc_id == "capa":
            seg.name, seg.doc_type, seg.type_source = "Capa do processo", "Capa", "estrutura_do_pdf"
            continue
        row = rows.get(doc_id)
        if row:
            seg.in_cover_table = True
            seg.name, seg.doc_type, seg.type_source = row.name, row.doc_type, row.type_source
            if row.date != DESCONHECIDO:
                seg.doc_date, seg.doc_date_source = row.date, "tabela_da_capa"
    return [segments[d] for d in order]
