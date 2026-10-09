"""Fixtures e dublês de teste. Nenhum teste padrão usa rede, Chatwoot real ou LLM real."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.models import (  # noqa: E402
    AccessClass, ChunkRecord, DocumentRecord, KnowledgeDomain, ReviewState,
)
from app.infrastructure.embeddings.embedders import HashingEmbedder  # noqa: E402
from app.infrastructure.sqlite.knowledge_store import SqliteKnowledgeStore  # noqa: E402
from app.infrastructure.sqlite.operational_store import OperationalStore  # noqa: E402


def pje_footer(doc_id: str, page: int, signer: str = "FULANO DE TAL", stamp: str = "12/05/2026 10:14:55") -> str:
    return (
        f"\n Assinado eletronicamente por: {signer} - {stamp}        Num. {doc_id} - Pág. {page}\n"
        f" https://pje.exemplo.jus.br:443/1g/Processo/ConsultaDocumento/listView.seam?x=2605121014550000000000{doc_id}\n"
        f" Número do documento: 2605121014550000000000{doc_id}\n"
    )


COVER = """            Tribunal de Justiça do Estado do Amapá
            PJe - Processo Judicial Eletrônico

                                                                         06/10/2026

Número: 1234567-89.2026.8.03.0001
Classe: PROCEDIMENTO COMUM CÍVEL
Órgão julgador: 2ª Vara Cível de Macapá
Última distribuição : 12/05/2026
Valor da causa: R$ 10.000,00
Assuntos: Indenização por dano moral
Segredo de justiça? NÃO
Justiça gratuita? SIM
                       Partes                                Procurador/Terceiro vinculado
MARIA EXEMPLO DA SILVA (AUTOR)                        JOAO ADVOGADO TESTE (ADVOGADO)
EMPRESA FICTICIA S.A (REU)
                                            Documentos
    Id.        Data     Documento                                           Tipo
 1000001 12/05/2026    Petição Inicial                                   Petição Inicial
         10:17
 1000002 14/05/2026    Decisão                                           Decisão
         20:43
 1000003 21/05/2026    Contracheque atual                                Documento de Comprovação
         02:21
 1000004 01/06/2026    Decisão                                           Decisão
         11:00
"""


def make_pdf_pages() -> list[str]:
    return [
        COVER,
        "Ao Juízo da 2ª Vara Cível. MARIA EXEMPLO DA SILVA, CPF 111.222.333-44, residente na Av. Teste, CEP 68900-000, "
        "propõe ação de indenização por danos morais contra EMPRESA FICTICIA S.A, no valor de R$ 10.000,00." + pje_footer("1000001", 1),
        "Decido. Defiro a gratuidade da justiça. Cite-se a ré para contestar em 15 dias. Macapá, 14/05/2026." + pje_footer("1000002", 1, "JUIZ TESTE", "14/05/2026 20:43:39"),
        "CONTRACHEQUE ATUAL. Remuneração bruta R$ 5.000,00." + pje_footer("1000003", 1),
        "Decido. Designo audiência de conciliação para 20/07/2026 às 10h. Intimem-se." + pje_footer("1000004", 1, "JUIZ TESTE", "01/06/2026 11:00:10"),
    ]


class FakeExtractor:
    def __init__(self, pages: list[str]) -> None:
        self.pages = pages
        self.calls = 0

    def extract_pages(self, path: Path) -> list[str]:
        self.calls += 1
        return list(self.pages)


class FakeOcr:
    def __init__(self, text: str = "", conf: float = 90.0) -> None:
        self.text, self.conf, self.calls = text, conf, []

    def ocr_page(self, path: Path, page: int):
        from app.domain.ports import OcrResult

        self.calls.append(page)
        return OcrResult(self.text, self.conf)


class FakeLLM:
    """LLM roteirizado: devolve respostas pré-definidas e guarda os prompts recebidos."""

    def __init__(self, *outputs: str, error: Exception | None = None) -> None:
        self.outputs, self.error, self.prompts = list(outputs), error, []

    def generate(self, *, system: str, user: str) -> str:
        self.prompts.append((system, user))
        if self.error:
            raise self.error
        return self.outputs.pop(0) if len(self.outputs) > 1 else self.outputs[0]


class FakeGateway:
    def __init__(self, fail_assign: int = 0, fail_send: int = 0) -> None:
        self.sent: list[tuple[int, str]] = []
        self.notes: list[tuple[int, str]] = []
        self.waited: list[int] = []
        self.options: list[tuple[int, list[str] | None]] = []
        self.labels: dict[int, list[str]] = {}
        self.assigned: list[tuple[int, str]] = []
        self.statuses: list[tuple[int, str]] = []
        self.fail_assign, self.fail_send = fail_assign, fail_send

    def send_message(self, account_id, conversation_id, content, options=None, private=False):
        if self.fail_send:
            self.fail_send -= 1
            raise RuntimeError("chatwoot fora do ar")
        if private:
            self.notes.append((conversation_id, content))
            return 1000 + len(self.notes)
        self.sent.append((conversation_id, content))
        self.options.append((conversation_id, [o.title for o in options] if options else None))
        return len(self.sent)

    def wait_dispatched(self, account_id, conversation_id, message_id, **kw):
        self.waited.append(message_id)
        return True

    def add_labels(self, account_id, conversation_id, labels):
        cur = self.labels.setdefault(conversation_id, [])
        cur.extend(x for x in labels if x not in cur)

    def assign_team(self, account_id, conversation_id, team_name):
        if self.fail_assign:
            self.fail_assign -= 1
            raise RuntimeError("chatwoot fora do ar")
        self.assigned.append((conversation_id, team_name))
        return True

    def set_status(self, account_id, conversation_id, status):
        self.statuses.append((conversation_id, status))
        return True


def add_doc(store: SqliteKnowledgeStore, embedder, *, doc_id: str, text: str, domain=KnowledgeDomain.PROCESSUAL, state=ReviewState.APPROVED,
            access=AccessClass.PUBLIC, process_number: str | None = "1234567-89.2026.8.03.0001", title="Decisão", doc_type="Decisão",
            doc_date="2026-05-14", url: str | None = None, pje_doc_id: str | None = "1000002", index: bool = True) -> None:
    doc = DocumentRecord(
        doc_id=doc_id, domain=domain, title=title, doc_type=doc_type, process_key=f"proc-{process_number}" if process_number else None,
        process_number=process_number, pje_doc_id=pje_doc_id, doc_date=doc_date, source_file="teste.pdf", source_url=url,
        issuing_body="Órgão Teste", access_class=access, review_state=state, content_hash=doc_id, page_start=2, page_end=2,
        collected_at="2026-10-01T00:00:00+00:00",
    )
    chunk = ChunkRecord(doc_id, domain, 0, 2, text, f"Processo {process_number} | {doc_type}", f"h-{doc_id}", pje_page=1)
    store.upsert_document(doc, [chunk])
    if process_number:
        store._exec(
            "INSERT OR IGNORE INTO processes(process_key, process_number, process_class, court_unit, subjects, pages, source_file) "
            "VALUES(?,?,?,?,?,?,?)", (f"proc-{process_number}", process_number, "PROCEDIMENTO COMUM CÍVEL", "2ª Vara Cível de Macapá", "Indenização", 10, "teste.pdf"),
        )
        store._conn.commit()
    if index:
        store.index_pending(embedder, doc_id=doc_id)


@pytest.fixture
def embedder():
    return HashingEmbedder()


@pytest.fixture
def store():
    s = SqliteKnowledgeStore(":memory:")
    yield s
    s.close()


@pytest.fixture
def ops():
    return OperationalStore(":memory:")
