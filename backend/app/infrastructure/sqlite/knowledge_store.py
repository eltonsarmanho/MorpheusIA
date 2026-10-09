"""Armazenamento do conhecimento em SQLite: metadados, FTS5 (BM25) e vetores em BLOB.

Elegibilidade (RAG-02, RAG-03): só entram em busca documentos `approved`, `public` e ativos.
Os índices contêm apenas trechos elegíveis e, ainda assim, toda leitura reaplica o filtro.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import unicodedata
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from app.domain.models import (
    AccessClass,
    ChunkRecord,
    DocumentRecord,
    Evidence,
    KnowledgeDomain,
    ReviewState,
)
from app.domain.ports import Embedder

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS processes (
  process_key TEXT PRIMARY KEY, process_number TEXT, source_file TEXT, sha256 TEXT, size_bytes INTEGER,
  pages INTEGER, tribunal TEXT, process_class TEXT, court_unit TEXT, distribution_date TEXT, case_value TEXT,
  subjects TEXT, secrecy TEXT, free_justice TEXT, parties TEXT, ingested_at TEXT, report TEXT
);
CREATE TABLE IF NOT EXISTS pages (
  process_key TEXT, pdf_page INTEGER, pje_doc_id TEXT, pje_page INTEGER, method TEXT, chars INTEGER,
  ocr_conf REAL, status TEXT, PRIMARY KEY (process_key, pdf_page)
);
CREATE TABLE IF NOT EXISTS ocr_cache (sha256 TEXT, page INTEGER, text TEXT, conf REAL, PRIMARY KEY (sha256, page));
CREATE TABLE IF NOT EXISTS documents (
  doc_id TEXT PRIMARY KEY, domain TEXT NOT NULL, title TEXT, doc_type TEXT, process_key TEXT, process_number TEXT,
  pje_doc_id TEXT, doc_date TEXT, doc_date_source TEXT, signed_at TEXT, published_at TEXT, source_file TEXT,
  source_url TEXT, issuing_body TEXT, page_start INTEGER, page_end INTEGER, access_class TEXT, review_state TEXT,
  review_reason TEXT, content_hash TEXT, indexed_at TEXT, collected_at TEXT, valid_from TEXT, valid_until TEXT,
  pii_counts TEXT, extra TEXT, active INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_documents_process ON documents(process_key);
CREATE INDEX IF NOT EXISTS ix_documents_state ON documents(domain, review_state);
CREATE TABLE IF NOT EXISTS chunks (
  chunk_id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id TEXT NOT NULL, domain TEXT NOT NULL, seq INTEGER, page INTEGER,
  pje_page INTEGER, text TEXT NOT NULL, ctx TEXT, content_hash TEXT, duplicate_of INTEGER, indexed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_chunks_doc ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS ix_chunks_hash ON chunks(content_hash);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, ctx, tokenize='unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS embeddings (chunk_id INTEGER PRIMARY KEY, vec BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS curation_decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id TEXT, content_hash TEXT, decision TEXT, reviewer TEXT, reason TEXT,
  access_class TEXT, decided_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_decisions_doc ON curation_decisions(doc_id, content_hash);
CREATE TABLE IF NOT EXISTS collection_log (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, url TEXT, domain TEXT, outcome TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS ingestion_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT, report TEXT);
"""

_ELIGIBLE = "d.review_state = 'approved' AND d.access_class = 'public' AND d.active = 1"
_STOP = frozenset(
    "a o as os um uma de do da dos das em no na nos nas por para com sem sobre entre e ou que qual quais como quando onde "
    "se ao aos à às é são foi ser ter tem há me meu minha seu sua isso este esta esse essa qual quem pode posso quero".split()
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


def query_terms(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", fold(text))
    return [t for t in tokens if t not in _STOP and (len(t) > 2 or t.isdigit())]


def fts_query(text: str) -> str:
    terms = list(dict.fromkeys(query_terms(text)))
    return " OR ".join(f'"{t}"' for t in terms[:24])


class _Rows(list):
    def fetchone(self):
        return self[0] if self else None

    def fetchall(self):
        return list(self)


class SqliteKnowledgeStore:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=OFF")
            self._conn.executescript(SCHEMA)
        self._matrix: np.ndarray | None = None
        self._ids: np.ndarray | None = None
        self._domains: np.ndarray | None = None
        self._proc: np.ndarray | None = None
        self._dirty = True

    # ------------------------------------------------------------------ util
    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _exec(self, sql: str, params: Sequence[Any] = ()) -> "_Rows":
        """Executa e busca as linhas sob o mesmo lock: a conexão é compartilhada entre threads."""
        with self._lock:
            cur = self._conn.execute(sql, params)
            return _Rows(cur.fetchall() if cur.description else [])

    def log_collection(self, url: str, domain: str, outcome: str, detail: str = "") -> None:
        with self._lock, self._conn:
            self._conn.execute("INSERT INTO collection_log(at,url,domain,outcome,detail) VALUES(?,?,?,?,?)", (_now(), url, domain, outcome, detail[:500]))

    def collection_log_rows(self, limit: int = 100) -> list[dict[str, Any]]:
        return [dict(r) for r in self._exec("SELECT * FROM collection_log ORDER BY id DESC LIMIT ?", (limit,))]

    def find_by_hash(self, content_hash: str, exclude_doc_id: str) -> DocumentRecord | None:
        r = self._exec(
            "SELECT * FROM documents WHERE content_hash=? AND doc_id!=? AND review_state!='rejected' LIMIT 1", (content_hash, exclude_doc_id)
        ).fetchone()
        return self._row_to_doc(r) if r else None

    def documents_by_domain_state(self, domain: KnowledgeDomain, state: ReviewState) -> list[DocumentRecord]:
        return self.list_documents(domain=domain, state=state, limit=100000)

    def get_meta(self, key: str) -> str | None:
        row = self._exec("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    # ------------------------------------------------------------ ingestion
    def get_process_sha(self, process_key: str) -> str | None:
        row = self._exec("SELECT sha256 FROM processes WHERE process_key=?", (process_key,)).fetchone()
        return row["sha256"] if row else None

    def get_ocr_cache(self, sha256: str, page: int) -> tuple[str, float] | None:
        row = self._exec("SELECT text, conf FROM ocr_cache WHERE sha256=? AND page=?", (sha256, page)).fetchone()
        return (row["text"], row["conf"]) if row else None

    def put_ocr_cache(self, sha256: str, page: int, text: str, conf: float) -> None:
        with self._lock, self._conn:
            self._conn.execute("INSERT OR REPLACE INTO ocr_cache VALUES(?,?,?,?)", (sha256, page, text, conf))

    def replace_process(self, process: dict[str, Any], pages: list[dict[str, Any]]) -> None:
        key = process["process_key"]
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM pages WHERE process_key=?", (key,))
            self._conn.execute("DELETE FROM processes WHERE process_key=?", (key,))
            cols = list(process)
            self._conn.execute(
                f"INSERT INTO processes({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                [json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for v in process.values()],
            )
            self._conn.executemany(
                "INSERT INTO pages VALUES(:process_key,:pdf_page,:pje_doc_id,:pje_page,:method,:chars,:ocr_conf,:status)", pages
            )

    def lookup_decision(self, doc_id: str, content_hash: str) -> dict[str, Any] | None:
        row = self._exec(
            "SELECT * FROM curation_decisions WHERE doc_id=? AND content_hash=? ORDER BY id DESC LIMIT 1",
            (doc_id, content_hash),
        ).fetchone()
        return dict(row) if row else None

    def replace_documents(
        self,
        process_key: str,
        docs: list[DocumentRecord],
        chunks: list[ChunkRecord],
    ) -> None:
        """Substitui todos os documentos e trechos de um processo (ING-09)."""
        with self._lock, self._conn:
            old = [r["doc_id"] for r in self._conn.execute("SELECT doc_id FROM documents WHERE process_key=?", (process_key,))]
            for doc_id in old:
                self._drop_index(doc_id)
            self._conn.execute("DELETE FROM chunks WHERE doc_id IN (SELECT doc_id FROM documents WHERE process_key=?)", (process_key,))
            self._conn.execute("DELETE FROM documents WHERE process_key=?", (process_key,))
            for d in docs:
                self._insert_document(d)
            self._insert_chunks(chunks)
        self._dirty = True

    def upsert_document(self, doc: DocumentRecord, chunks: list[ChunkRecord]) -> None:
        """Insere ou substitui um documento isolado (coletores B/C)."""
        with self._lock, self._conn:
            self._drop_index(doc.doc_id)
            self._conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc.doc_id,))
            self._conn.execute("DELETE FROM documents WHERE doc_id=?", (doc.doc_id,))
            self._insert_document(doc)
            self._insert_chunks(chunks)
        self._dirty = True

    def _insert_document(self, d: DocumentRecord) -> None:
        row = asdict(d)
        row["domain"] = d.domain.value
        row["access_class"] = d.access_class.value
        row["review_state"] = d.review_state.value
        row["pii_counts"] = json.dumps(d.pii_counts, ensure_ascii=False)
        row["extra"] = json.dumps(d.extra, ensure_ascii=False, default=str)
        cols = list(row)
        self._conn.execute(
            f"INSERT INTO documents({','.join(cols)}) VALUES({','.join('?' * len(cols))})", [row[c] for c in cols]
        )

    def _insert_chunks(self, chunks: list[ChunkRecord]) -> None:
        for c in chunks:
            self._conn.execute(
                "INSERT INTO chunks(doc_id,domain,seq,page,pje_page,text,ctx,content_hash,duplicate_of,indexed) VALUES(?,?,?,?,?,?,?,?,NULL,0)",
                (c.doc_id, c.domain.value, c.seq, c.page, c.pje_page, c.text, c.context, c.content_hash),
            )

    # ----------------------------------------------------------- indexação
    def _drop_index(self, doc_id: str) -> None:
        ids = [r["chunk_id"] for r in self._conn.execute("SELECT chunk_id FROM chunks WHERE doc_id=?", (doc_id,))]
        if not ids:
            return
        marks = ",".join("?" * len(ids))
        self._conn.execute(f"DELETE FROM chunks_fts WHERE rowid IN ({marks})", ids)
        self._conn.execute(f"DELETE FROM embeddings WHERE chunk_id IN ({marks})", ids)
        self._conn.execute(f"UPDATE chunks SET indexed=0 WHERE chunk_id IN ({marks})", ids)

    def deindex_document(self, doc_id: str) -> None:
        with self._lock, self._conn:
            self._drop_index(doc_id)
        self._dirty = True

    def index_document(self, doc_id: str, embedder: Embedder, batch_size: int = 64) -> int:
        return self.index_pending(embedder, doc_id=doc_id, batch_size=batch_size)

    def index_pending(
        self, embedder: Embedder, *, doc_id: str | None = None, process_key: str | None = None, batch_size: int = 64,
        progress=None,
    ) -> int:
        """Indexa (FTS + vetor) trechos não duplicados e ainda não indexados de documentos elegíveis."""
        sql = (
            "SELECT c.chunk_id, c.text, c.ctx FROM chunks c JOIN documents d ON d.doc_id=c.doc_id "
            f"WHERE c.duplicate_of IS NULL AND c.indexed=0 AND {_ELIGIBLE}"
        )
        params: list[Any] = []
        if doc_id:
            sql += " AND c.doc_id=?"; params.append(doc_id)
        if process_key:
            sql += " AND d.process_key=?"; params.append(process_key)
        sql += " ORDER BY c.chunk_id"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        total = 0
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            texts = [f"{r['ctx']}\n{r['text']}" if r["ctx"] else r["text"] for r in batch]
            vectors = embedder.embed_documents(texts)
            with self._lock, self._conn:
                for r, v in zip(batch, vectors):
                    self._conn.execute("INSERT INTO chunks_fts(rowid,text,ctx) VALUES(?,?,?)", (r["chunk_id"], r["text"], r["ctx"] or ""))
                    self._conn.execute("INSERT OR REPLACE INTO embeddings VALUES(?,?)", (r["chunk_id"], v.astype(np.float32).tobytes()))
                    self._conn.execute("UPDATE chunks SET indexed=1 WHERE chunk_id=?", (r["chunk_id"],))
            total += len(batch)
            if progress:
                progress(total, len(rows))
        if total:
            self._dirty = True
        return total

    def mark_duplicates(self, process_key: str) -> int:
        """Marca trechos repetidos (mesmo hash) dentro do processo; mantém o primeiro (ING-08)."""
        with self._lock, self._conn:
            rows = self._conn.execute(
                "SELECT c.chunk_id, c.content_hash FROM chunks c JOIN documents d ON d.doc_id=c.doc_id "
                "WHERE d.process_key=? ORDER BY c.chunk_id",
                (process_key,),
            ).fetchall()
            seen: dict[str, int] = {}
            dups = 0
            for r in rows:
                first = seen.setdefault(r["content_hash"], r["chunk_id"])
                if first != r["chunk_id"]:
                    self._conn.execute("UPDATE chunks SET duplicate_of=? WHERE chunk_id=?", (first, r["chunk_id"]))
                    dups += 1
        return dups

    # ------------------------------------------------------------- curadoria
    def set_review(
        self, doc_id: str, state: ReviewState, *, reviewer: str, reason: str, access_class: str | None = None
    ) -> None:
        with self._lock, self._conn:
            row = self._conn.execute("SELECT content_hash, access_class FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
            if row is None:
                raise KeyError(doc_id)
            new_access = access_class or (AccessClass.PUBLIC.value if state is ReviewState.APPROVED else row["access_class"])
            self._conn.execute(
                "UPDATE documents SET review_state=?, review_reason=?, access_class=?, active=? WHERE doc_id=?",
                (state.value, reason, new_access, 0 if state in (ReviewState.REJECTED, ReviewState.STALE) else 1, doc_id),
            )
            self._conn.execute(
                "INSERT INTO curation_decisions(doc_id,content_hash,decision,reviewer,reason,access_class,decided_at) VALUES(?,?,?,?,?,?,?)",
                (doc_id, row["content_hash"], state.value, reviewer, reason, new_access, _now()),
            )
            if state is not ReviewState.APPROVED:
                self._drop_index(doc_id)
        self._dirty = True

    def mark_review_system(self, doc_id: str, state: ReviewState, reason: str) -> None:
        """Mudança automática (ex.: stale), sem decisão humana registrada."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE documents SET review_state=?, review_reason=?, active=? WHERE doc_id=?",
                (state.value, reason, 0 if state in (ReviewState.REJECTED, ReviewState.STALE) else 1, doc_id),
            )
            if state is not ReviewState.APPROVED:
                self._drop_index(doc_id)
        self._dirty = True

    # --------------------------------------------------------------- leitura
    @staticmethod
    def _row_to_doc(r: sqlite3.Row) -> DocumentRecord:
        return DocumentRecord(
            doc_id=r["doc_id"], domain=KnowledgeDomain(r["domain"]), title=r["title"] or "", doc_type=r["doc_type"] or "",
            process_key=r["process_key"], process_number=r["process_number"], pje_doc_id=r["pje_doc_id"],
            doc_date=r["doc_date"] or "", doc_date_source=r["doc_date_source"] or "", signed_at=r["signed_at"] or "",
            published_at=r["published_at"] or "", source_file=r["source_file"], source_url=r["source_url"],
            issuing_body=r["issuing_body"] or "", page_start=r["page_start"], page_end=r["page_end"],
            access_class=AccessClass(r["access_class"]), review_state=ReviewState(r["review_state"]),
            review_reason=r["review_reason"] or "", content_hash=r["content_hash"] or "", indexed_at=r["indexed_at"] or "",
            collected_at=r["collected_at"] or "", valid_from=r["valid_from"] or "", valid_until=r["valid_until"] or "",
            pii_counts=json.loads(r["pii_counts"] or "{}"), extra=json.loads(r["extra"] or "{}"),
        )

    def get_document(self, doc_id: str) -> DocumentRecord | None:
        r = self._exec("SELECT * FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
        return self._row_to_doc(r) if r else None

    def list_documents(
        self, *, domain: KnowledgeDomain | None = None, state: ReviewState | None = None, limit: int = 200, offset: int = 0,
        process_number: str | None = None,
    ) -> list[DocumentRecord]:
        sql, params = "SELECT * FROM documents WHERE 1=1", []
        if domain:
            sql += " AND domain=?"; params.append(domain.value)
        if state:
            sql += " AND review_state=?"; params.append(state.value)
        if process_number:
            sql += " AND process_number=?"; params.append(process_number)
        sql += " ORDER BY process_number, page_start, doc_id LIMIT ? OFFSET ?"
        params += [limit, offset]
        return [self._row_to_doc(r) for r in self._exec(sql, params).fetchall()]

    def count_by_state(self) -> list[dict[str, Any]]:
        rows = self._exec(
            "SELECT domain, review_state, COUNT(*) n FROM documents GROUP BY domain, review_state ORDER BY domain, review_state"
        ).fetchall()
        return [dict(r) for r in rows]

    def known_process_numbers(self) -> list[str]:
        return [r["process_number"] for r in self._exec("SELECT process_number FROM processes ORDER BY process_number")]

    def process_info(self, process_number: str) -> dict[str, Any] | None:
        r = self._exec("SELECT * FROM processes WHERE process_number=?", (process_number,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["parties"] = json.loads(d.get("parties") or "[]")
        d["report"] = json.loads(d.get("report") or "{}")
        return d

    def indexed_chunk_count(self) -> int:
        return self._exec("SELECT COUNT(*) n FROM embeddings").fetchone()["n"]


    def approved_processes(self) -> list[dict[str, Any]]:
        """Processos cuja capa está aprovada para uso (ficha pública do acervo)."""
        rows = self._exec(
            "SELECT p.process_number, p.process_class, p.court_unit, p.subjects, p.pages, p.source_file FROM processes p "
            f"JOIN documents d ON d.process_key=p.process_key AND d.pje_doc_id IS NULL AND {_ELIGIBLE} "
            "ORDER BY p.process_number"
        ).fetchall()
        return [dict(r) for r in rows]

    def latest_document(self, process_number: str, type_like: str) -> DocumentRecord | None:
        """Documento mais recente por DATA DO DOCUMENTO (tabela da capa), nunca por data de indexação."""
        r = self._exec(
            "SELECT d.* FROM documents d WHERE d.process_number=? AND d.pje_doc_id IS NOT NULL AND d.doc_date != 'desconhecido' "
            f"AND lower(d.doc_type) LIKE ? AND {_ELIGIBLE} ORDER BY d.doc_date DESC, CAST(d.pje_doc_id AS INTEGER) DESC LIMIT 1",
            (process_number, f"%{type_like.lower()}%"),
        ).fetchone()
        return self._row_to_doc(r) if r else None

    def evidences_for_doc(self, doc_id: str, limit: int = 4) -> list[Evidence]:
        ids = [r["chunk_id"] for r in self._exec(
            "SELECT chunk_id FROM chunks WHERE doc_id=? AND duplicate_of IS NULL ORDER BY seq LIMIT ?", (doc_id, limit)
        )]
        dom = self._exec("SELECT domain FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
        if not dom:
            return []
        evs = self.load_evidences(ids, KnowledgeDomain(dom["domain"]))
        order = {c: i for i, c in enumerate(ids)}
        return sorted(evs, key=lambda e: order[e.chunk_id])

    # ----------------------------------------------------------------- busca
    def search_lexical(
        self, query: str, domain: KnowledgeDomain, limit: int, process_number: str | None = None
    ) -> list[tuple[int, float]]:
        q = fts_query(query)
        if not q:
            return []
        sql = (
            "SELECT f.rowid AS chunk_id, bm25(chunks_fts, 1.0, 0.4) AS s FROM chunks_fts f "
            "JOIN chunks c ON c.chunk_id=f.rowid JOIN documents d ON d.doc_id=c.doc_id "
            f"WHERE chunks_fts MATCH ? AND c.domain=? AND {_ELIGIBLE}"
        )
        params: list[Any] = [q, domain.value]
        if process_number:
            sql += " AND d.process_number=?"; params.append(process_number)
        sql += " ORDER BY s LIMIT ?"
        params.append(limit)
        try:
            rows = self._exec(sql, params).fetchall()
        except sqlite3.OperationalError:
            return []
        return [(r["chunk_id"], -float(r["s"])) for r in rows]

    def _reload_vectors(self) -> None:
        with self._lock:
            rows = self._conn.execute(
                "SELECT e.chunk_id, e.vec, c.domain, COALESCE(d.process_number,'') pn FROM embeddings e "
                "JOIN chunks c ON c.chunk_id=e.chunk_id JOIN documents d ON d.doc_id=c.doc_id "
                f"WHERE {_ELIGIBLE}"
            ).fetchall()
        if not rows:
            self._matrix, self._ids, self._domains, self._proc = None, None, None, None
        else:
            self._matrix = np.vstack([np.frombuffer(r["vec"], dtype=np.float32) for r in rows])
            norms = np.linalg.norm(self._matrix, axis=1, keepdims=True)
            self._matrix = self._matrix / np.maximum(norms, 1e-9)
            self._ids = np.array([r["chunk_id"] for r in rows], dtype=np.int64)
            self._domains = np.array([r["domain"] for r in rows])
            self._proc = np.array([r["pn"] for r in rows])
        self._dirty = False

    def search_vector(
        self, vector: np.ndarray, domain: KnowledgeDomain, limit: int, process_number: str | None = None
    ) -> list[tuple[int, float]]:
        if self._dirty:
            self._reload_vectors()
        if self._matrix is None:
            return []
        mask = self._domains == domain.value
        if process_number:
            mask &= self._proc == process_number
        idx = np.flatnonzero(mask)
        if idx.size == 0:
            return []
        v = vector.astype(np.float32)
        v = v / max(float(np.linalg.norm(v)), 1e-9)
        sims = self._matrix[idx] @ v
        top = np.argsort(-sims)[:limit]
        return [(int(self._ids[idx[i]]), float(sims[i])) for i in top]

    def load_evidences(self, chunk_ids: Sequence[int], domain: KnowledgeDomain) -> list[Evidence]:
        """Carrega trechos reaplicando o filtro de elegibilidade e de domínio (defesa em profundidade)."""
        if not chunk_ids:
            return []
        marks = ",".join("?" * len(chunk_ids))
        rows = self._exec(
            "SELECT c.chunk_id, c.doc_id, c.domain, c.text, c.page, c.pje_page, d.* FROM chunks c "
            f"JOIN documents d ON d.doc_id=c.doc_id WHERE c.chunk_id IN ({marks}) AND c.domain=? AND {_ELIGIBLE}",
            [*chunk_ids, domain.value],
        ).fetchall()
        out = []
        for r in rows:
            citation = {
                "titulo": r["title"] or "", "tipo": r["doc_type"] or "", "data": r["doc_date"] or "",
                "processo": r["process_number"] or "", "documento": r["pje_doc_id"] or "", "arquivo": r["source_file"] or "",
                "orgao": r["issuing_body"] or "", "url": r["source_url"] or "", "pagina_pdf": str(r["page"] or ""),
                "pagina_doc": str(r["pje_page"] or ""),
            }
            out.append(
                Evidence(chunk_id=r["chunk_id"], doc_id=r["doc_id"], domain=KnowledgeDomain(r["domain"]), text=r["text"],
                         page=r["page"], score=0.0, citation=citation)
            )
        return out
