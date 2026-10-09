"""Pipeline de ingestão dos PDFs de processos (ING-01 a ING-10, CUR-01 a CUR-03, SEC-01)."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from app.application.ingestion.pje_parser import (
    CoverInfo,
    DocumentSegment,
    PjePage,
    build_pages,
    parse_cover,
    segment_documents,
)
from app.application.ingestion.text_processing import chunk_text, content_hash, normalize_text
from app.domain.models import (
    DESCONHECIDO,
    AccessClass,
    ChunkRecord,
    DocumentRecord,
    KnowledgeDomain,
    ReviewState,
)
from app.domain.policies import triage_document
from app.domain.ports import Embedder, OcrEngine, PdfTextExtractor
from app.domain.privacy import mask_pii
from app.infrastructure.sqlite.knowledge_store import SqliteKnowledgeStore

log = logging.getLogger(__name__)

PAGE_OK, PAGE_NEEDS_REVIEW, PAGE_NO_TEXT = "ok", "needs_review", "no_text"


@dataclass
class IngestionConfig:
    corpus_authorized: bool = False
    authorization_basis: str = "nao_declarada"
    ocr_min_body_chars: int = 30
    ocr_min_confidence: float = 60.0
    ocr_workers: int = 8
    max_chunk_chars: int = 900
    garbled_ratio: float = 0.02


@dataclass
class FileReport:
    file: str
    sha256: str
    size_bytes: int
    pages: int = 0
    process_number: str = DESCONHECIDO
    status: str = "ok"  # ok | skipped_unchanged | error
    error: str = ""
    pages_text: int = 0
    pages_ocr: int = 0
    pages_needs_review: int = 0
    pages_no_text: int = 0
    documents: int = 0
    documents_approved: int = 0
    documents_pending: int = 0
    chunks: int = 0
    chunks_duplicate: int = 0
    chunks_indexed: int = 0
    pii_counts: dict[str, int] = field(default_factory=dict)
    duration_s: float = 0.0
    warnings: list[str] = field(default_factory=list)


def sha256_file(path: Path, block: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(block):
            h.update(chunk)
    return h.hexdigest()


def _chars(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def _is_garbled(text: str, ratio: float) -> bool:
    n = len(text)
    return n >= 100 and text.count("�") / n > ratio


def _dt(value: str) -> str:
    return value if value and value != DESCONHECIDO else DESCONHECIDO


class IngestionService:
    def __init__(
        self,
        store: SqliteKnowledgeStore,
        extractor: PdfTextExtractor,
        ocr: OcrEngine | None,
        embedder: Embedder,
        config: IngestionConfig,
        page_counter: Callable[[Path], int] | None = None,
    ) -> None:
        self.store, self.extractor, self.ocr, self.embedder, self.cfg = store, extractor, ocr, embedder, config
        self.page_counter = page_counter

    # ------------------------------------------------------------ inventário
    def inventory(self, directory: Path) -> list[dict]:
        """ING-01: SHA-256, tamanho e páginas por arquivo, com marcação de duplicatas exatas."""
        items, seen = [], {}
        for path in sorted(directory.glob("*.pdf")):
            sha = sha256_file(path)
            item = {
                "file": path.name, "sha256": sha, "size_bytes": path.stat().st_size,
                "pages": self.page_counter(path) if self.page_counter else None,
                "duplicate_of": seen.get(sha),
            }
            seen.setdefault(sha, path.name)
            items.append(item)
        return items

    # ----------------------------------------------------------- um arquivo
    def ingest_file(
        self, path: Path, *, force: bool = False, progress: Callable[[str], None] | None = None, sha: str | None = None
    ) -> FileReport:
        t0 = time.time()
        say = progress or (lambda _m: None)
        sha = sha or sha256_file(path)
        report = FileReport(file=path.name, sha256=sha, size_bytes=path.stat().st_size)
        process_key = f"proc-{path.stem}"
        try:
            if not force and self.store.get_process_sha(process_key) == sha:
                report.status = "skipped_unchanged"
                report.process_number = path.stem
                return report
            say(f"{path.name}: extraindo texto")
            texts = self.extractor.extract_pages(path)
            pages = build_pages(texts)
            report.pages = len(pages)
            cover_text = "\n".join(p.raw_text for p in pages if p.doc_id is None)
            cover = parse_cover(cover_text)
            number = cover.number if cover.number != DESCONHECIDO else path.stem
            if number != path.stem:
                report.warnings.append(f"número da capa ({number}) difere do nome do arquivo ({path.stem})")
            report.process_number = number
            page_rows = self._resolve_pages(path, sha, pages, report, say)
            segments = segment_documents(pages, cover)
            docs, chunks, doc_pii = self._build_documents(path, process_key, number, cover, pages, page_rows, segments)
            process = self._process_row(path, process_key, number, sha, report, cover)
            self.store.replace_process(process, [r for r in page_rows.values()])
            self.store.replace_documents(process_key, docs, chunks)
            report.chunks_duplicate = self.store.mark_duplicates(process_key)
            report.documents = len(docs)
            report.documents_approved = sum(1 for d in docs if d.review_state is ReviewState.APPROVED)
            report.documents_pending = report.documents - report.documents_approved
            report.chunks = len(chunks)
            for d in docs:
                for k, v in d.pii_counts.items():
                    report.pii_counts[k] = report.pii_counts.get(k, 0) + v
            say(f"{path.name}: indexando {len(chunks)} trechos")
            report.chunks_indexed = self.store.index_pending(
                self.embedder, process_key=process_key, progress=lambda a, b: say(f"{path.name}: vetores {a}/{b}") if a % 2048 < 64 else None
            )
        except Exception as exc:  # noqa: BLE001 - registrar e seguir com os demais arquivos
            log.exception("falha ao ingerir %s", path.name)
            report.status, report.error = "error", f"{type(exc).__name__}: {exc}"[:300]
        report.duration_s = round(time.time() - t0, 1)
        return report

    # ------------------------------------------------------------- páginas
    def _resolve_pages(self, path: Path, sha: str, pages: list[PjePage], report: FileReport, say) -> dict[int, dict]:
        """Decide texto vs OCR por página, medindo o corpo sem o rodapé (ING-03, ING-04)."""
        rows: dict[int, dict] = {}
        need_ocr: list[PjePage] = []
        for p in pages:
            body_chars = _chars(p.body)
            if body_chars < self.cfg.ocr_min_body_chars and self.ocr is not None:
                need_ocr.append(p)
                continue
            status = PAGE_OK
            if _is_garbled(p.body, self.cfg.garbled_ratio):
                status = PAGE_NEEDS_REVIEW
            rows[p.pdf_page] = self._page_row(path, p, "text", body_chars, None, status)
        if need_ocr:
            say(f"{path.name}: OCR em {len(need_ocr)} páginas")

            def work(p: PjePage):
                cached = self.store.get_ocr_cache(sha, p.pdf_page)
                if cached:
                    return p, cached[0], cached[1]
                res = self.ocr.ocr_page(path, p.pdf_page)  # type: ignore[union-attr]
                self.store.put_ocr_cache(sha, p.pdf_page, res.text, res.mean_confidence)
                return p, res.text, res.mean_confidence

            with ThreadPoolExecutor(self.cfg.ocr_workers) as pool:
                for p, text, conf in pool.map(work, need_ocr):
                    chars = _chars(text)
                    if chars < 10:
                        status = PAGE_NO_TEXT
                    elif conf < self.cfg.ocr_min_confidence:
                        status = PAGE_NEEDS_REVIEW
                    else:
                        status = PAGE_OK
                    p.body = (p.body.strip() + "\n" + text).strip() if status != PAGE_NO_TEXT else p.body
                    rows[p.pdf_page] = self._page_row(path, p, "ocr", chars, conf, status)
        for p in pages:
            if p.pdf_page not in rows:  # sem OCR configurado
                rows[p.pdf_page] = self._page_row(path, p, "text", _chars(p.body), None, PAGE_NO_TEXT)
        for r in rows.values():
            if r["method"] == "ocr":
                report.pages_ocr += 1
            else:
                report.pages_text += 1
            report.pages_needs_review += r["status"] == PAGE_NEEDS_REVIEW
            report.pages_no_text += r["status"] == PAGE_NO_TEXT
        return dict(sorted(rows.items()))

    @staticmethod
    def _page_row(path: Path, p: PjePage, method: str, chars: int, conf: float | None, status: str) -> dict:
        return {
            "process_key": f"proc-{path.stem}", "pdf_page": p.pdf_page, "pje_doc_id": p.doc_id, "pje_page": p.pje_page,
            "method": method, "chars": chars, "ocr_conf": conf, "status": status,
        }

    # ----------------------------------------------------------- documentos
    def _build_documents(
        self, path: Path, process_key: str, number: str, cover: CoverInfo, pages: list[PjePage],
        page_rows: dict[int, dict], segments: list[DocumentSegment],
    ) -> tuple[list[DocumentRecord], list[ChunkRecord], dict]:
        by_page = {p.pdf_page: p for p in pages}
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        docs: list[DocumentRecord] = []
        chunks: list[ChunkRecord] = []
        hidden: set[str] = set()  # documentos sensíveis: a capa não revela o nome (CUR-02)
        ordered = [s for s in segments if s.pje_doc_id != "capa"] + [s for s in segments if s.pje_doc_id == "capa"]
        for seg in ordered:
            doc_id = f"{process_key}:{seg.pje_doc_id}"
            ctx = " | ".join(
                x for x in (f"Processo {number}", seg.doc_type if seg.doc_type != DESCONHECIDO else "", seg.name,
                            seg.doc_date if seg.doc_date != DESCONHECIDO else "") if x
            )
            page_texts: list[tuple[int, int | None, str]] = []
            review_pages: list[int] = []
            if seg.pje_doc_id == "capa":
                synthetic = self._cover_chunks(cover, number, hidden)
                if synthetic:
                    page_texts.append((seg.pdf_pages[0], None, synthetic))
            for pn in seg.pdf_pages:
                if seg.pje_doc_id == "capa" and page_texts:
                    continue  # a ficha sintetizada da capa substitui o layout bruto
                row = page_rows[pn]
                if row["status"] == PAGE_NEEDS_REVIEW:
                    review_pages.append(pn)
                    continue
                if row["status"] == PAGE_NO_TEXT:
                    continue
                page_texts.append((pn, by_page[pn].pje_page, normalize_text(by_page[pn].body)))
            joined = "\n".join(t for _, _, t in page_texts)
            masked_pages: list[tuple[int, int | None, str]] = []
            pii: dict[str, int] = {}
            for pn, jp, t in page_texts:
                m, counts = mask_pii(t)
                masked_pages.append((pn, jp, m))
                for k, v in counts.items():
                    pii[k] = pii.get(k, 0) + v
            c_hash = content_hash(joined) if joined else content_hash(doc_id)
            triage = triage_document(
                doc_type=seg.doc_type, doc_name=seg.name, cover_secrecy=cover.secrecy if cover.secrecy != DESCONHECIDO else None,
                body_text=joined[:20000], corpus_authorized=self.cfg.corpus_authorized,
            )
            state, access, reason = triage.review_state, triage.access_class, triage.reason
            if not joined:
                state, access, reason = ReviewState.PENDING_REVIEW, AccessClass.UNKNOWN, "documento sem texto legível"
            decision = self.store.lookup_decision(doc_id, c_hash)
            if decision:  # decisão humana anterior para o mesmo conteúdo prevalece (CUR-03)
                state, access = ReviewState(decision["decision"]), AccessClass(decision["access_class"] or access)
                reason = f"decisão humana preservada ({decision['reviewer']}, {decision['decided_at']}): {decision['reason']}"
            elif state is ReviewState.APPROVED:
                reason = f"{reason} [base: {self.cfg.authorization_basis}]"
            extra: dict[str, object] = {
                "type_source": seg.type_source, "in_cover_table": seg.in_cover_table,
                "pdf_pages": [seg.pdf_pages[0], seg.pdf_pages[-1]], "signer": seg.signer,
                "pages_needs_review": review_pages, "classe": cover.process_class, "orgao_julgador": cover.court_unit,
                "tribunal_origem": cover.tribunal,
            }
            if access is AccessClass.RESTRICTED:
                hidden.add(seg.pje_doc_id)
            docs.append(
                DocumentRecord(
                    doc_id=doc_id, domain=KnowledgeDomain.PROCESSUAL, title=seg.name, doc_type=seg.doc_type,
                    process_key=process_key, process_number=number,
                    pje_doc_id=None if seg.pje_doc_id == "capa" else seg.pje_doc_id, doc_date=seg.doc_date,
                    doc_date_source=seg.doc_date_source, signed_at=_dt(seg.signed_at), published_at=DESCONHECIDO,
                    source_file=path.name, source_url=None, issuing_body=cover.court_unit,
                    page_start=seg.pdf_pages[0], page_end=seg.pdf_pages[-1], access_class=access, review_state=state,
                    review_reason=reason, content_hash=c_hash, indexed_at=now, collected_at=now, pii_counts=pii, extra=extra,
                )
            )
            seq = 0
            for pn, jp, t in masked_pages:
                for piece in chunk_text(t, self.cfg.max_chunk_chars):
                    chunks.append(
                        ChunkRecord(doc_id, KnowledgeDomain.PROCESSUAL, seq, pn, piece, ctx, content_hash(piece), pje_page=jp)
                    )
                    seq += 1
        order = {sg.pje_doc_id: i for i, sg in enumerate(segments)}
        docs.sort(key=lambda d: order.get(d.pje_doc_id or 'capa', 0))
        return docs, chunks, {}

    @staticmethod
    def _cover_chunks(cover: CoverInfo, number: str, hidden: set[str] | None = None) -> str:
        if cover.number == DESCONHECIDO:
            return ""
        lines = [
            f"Ficha do processo {number} (dados extraídos da capa do PDF)",
            f"Tribunal: {cover.tribunal}", f"Classe: {cover.process_class}", f"Órgão julgador: {cover.court_unit}",
            f"Última distribuição: {cover.distribution_date}", f"Valor da causa: {cover.case_value}",
            f"Assuntos: {cover.subjects}", f"Segredo de justiça: {cover.secrecy}", f"Justiça gratuita: {cover.free_justice}",
        ]
        if cover.parties:
            lines.append("Partes e representantes: " + "; ".join(f"{p['nome']} ({p['papel']})" for p in cover.parties))
        if cover.rows:
            lines.append("")
            lines.append("Cronologia dos documentos listados na capa (data da juntada; tipo; documento; id):")
            for r in sorted(cover.rows, key=lambda r: (r.date, r.time)):
                if hidden and r.doc_id in hidden:
                    lines.append(f"{r.date} {r.time}; documento com acesso em revisão; id {r.doc_id}")
                else:
                    lines.append(f"{r.date} {r.time}; {r.doc_type}; {r.name}; id {r.doc_id}")
        return "\n\n".join(lines)

    @staticmethod
    def _process_row(path: Path, key: str, number: str, sha: str, report: FileReport, cover: CoverInfo) -> dict:
        return {
            "process_key": key, "process_number": number, "source_file": path.name, "sha256": sha,
            "size_bytes": report.size_bytes, "pages": report.pages, "tribunal": cover.tribunal,
            "process_class": cover.process_class, "court_unit": cover.court_unit, "distribution_date": cover.distribution_date,
            "case_value": cover.case_value, "subjects": cover.subjects, "secrecy": cover.secrecy,
            "free_justice": cover.free_justice, "parties": cover.parties,
            "ingested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "report": asdict(report),
        }

    # ----------------------------------------------------------- diretório
    def ingest_dir(
        self, directory: Path, *, force: bool = False, only: list[str] | None = None, progress: Callable[[str], None] | None = None,
        report_dir: Path | None = None,
    ) -> dict:
        t0 = time.time()
        started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        inv = self.inventory(directory)
        dups = {i["file"]: i["duplicate_of"] for i in inv if i["duplicate_of"]}
        shas = {i["file"]: i["sha256"] for i in inv}
        files = [directory / i["file"] for i in inv if i["file"] not in dups and (not only or any(o in i["file"] for o in only))]
        reports = [self.ingest_file(p, force=force, progress=progress, sha=shas[p.name]) for p in files]
        summary = {
            "started_at": started, "duration_s": round(time.time() - t0, 1), "files": len(inv),
            "duplicate_files": dups, "authorization": {"authorized": self.cfg.corpus_authorized, "basis": self.cfg.authorization_basis},
            "embedding_model": self.embedder.name,
            "totals": {
                "pages": sum(r.pages for r in reports), "pages_ocr": sum(r.pages_ocr for r in reports),
                "pages_needs_review": sum(r.pages_needs_review for r in reports), "pages_no_text": sum(r.pages_no_text for r in reports),
                "documents": sum(r.documents for r in reports), "documents_approved": sum(r.documents_approved for r in reports),
                "documents_pending": sum(r.documents_pending for r in reports), "chunks": sum(r.chunks for r in reports),
                "chunks_duplicate": sum(r.chunks_duplicate for r in reports), "chunks_indexed": sum(r.chunks_indexed for r in reports),
                "errors": sum(1 for r in reports if r.status == "error"),
                "skipped_unchanged": sum(1 for r in reports if r.status == "skipped_unchanged"),
            },
            "inventory": inv, "reports": [asdict(r) for r in reports],
        }
        self.store.set_meta("embedding_model", self.embedder.name)
        if report_dir:
            report_dir.mkdir(parents=True, exist_ok=True)
            out = report_dir / f"ingestion-{started.replace(':', '').replace('-', '')}.json"
            out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            summary["report_path"] = str(out)
        return summary
