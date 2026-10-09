"""Linha de comando: ingestão, curadoria, busca e avaliação (`python -m app.interfaces.cli --help`)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.config import get_settings
from app.domain.models import KnowledgeDomain, ReviewState


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))




def cmd_ingest(args) -> int:
    from app.container import build_embedder, build_ingestion, build_store
    s = get_settings()
    store = build_store(s)
    svc = build_ingestion(s, store, build_embedder(s))
    summary = svc.ingest_dir(
        Path(args.dir or s.processes_dir), force=args.force, only=args.only, progress=lambda m: print(m, file=sys.stderr, flush=True),
        report_dir=s.data_dir / "reports",
    )
    summary.pop("inventory", None)
    for r in summary["reports"]:
        r.pop("warnings", None) if not r.get("warnings") else None
    _print({k: v for k, v in summary.items() if k != "reports"})
    for r in summary["reports"]:
        print(f"{r['file']}: {r['status']} paginas={r['pages']} ocr={r['pages_ocr']} revisar={r['pages_needs_review']} "
              f"docs={r['documents']} aprov={r['documents_approved']} pend={r['documents_pending']} trechos={r['chunks']} "
              f"dup={r['chunks_duplicate']} {r['duration_s']}s {r['error']}")
    return 1 if summary["totals"]["errors"] else 0


def cmd_stats(_args) -> int:
    from app.container import build_store
    store = build_store(get_settings())
    _print({"documentos_por_estado": store.count_by_state(), "trechos_indexados": store.indexed_chunk_count(),
            "processos": store.known_process_numbers()})
    return 0


def cmd_docs(args) -> int:
    from app.container import build_store
    store = build_store(get_settings())
    docs = store.list_documents(
        domain=KnowledgeDomain(args.domain) if args.domain else None,
        state=ReviewState(args.state) if args.state else None, limit=args.limit, process_number=args.process,
    )
    for d in docs:
        print(f"{d.doc_id}\t{d.review_state.value}\t{d.access_class.value}\t{d.doc_type}\t{d.title[:50]}\t{d.review_reason[:70]}")
    print(f"-- {len(docs)} documentos", file=sys.stderr)
    return 0


def _review(args, state: ReviewState) -> int:
    from app.container import build_embedder, build_store
    s = get_settings()
    store = build_store(s)
    ids = args.doc_id
    if args.process:
        ids = [d.doc_id for d in store.list_documents(state=ReviewState.PENDING_REVIEW, process_number=args.process, limit=100000)]
    for doc_id in ids:
        store.set_review(doc_id, state, reviewer=args.reviewer, reason=args.reason)
    if state is ReviewState.APPROVED:
        embedder = build_embedder(s)
        for doc_id in ids:
            store.index_pending(embedder, doc_id=doc_id)
    print(f"{len(ids)} documento(s) -> {state.value}")
    return 0


def cmd_collect(args) -> int:
    from app.container import build_collection, build_store
    s = get_settings()
    store = build_store(s)
    svc = build_collection(s, store)
    if args.url:
        out = [svc.collect_url(KnowledgeDomain(args.domain), args.url)]
    else:
        out = svc.collect_all()
    _print(out)
    return 0


def cmd_search(args) -> int:
    from app.container import build_container
    s = get_settings()
    c = build_container(s, llm=None, gateway=None)
    res = c.retriever.retrieve(args.query, KnowledgeDomain(args.domain), process_number=args.process)
    print(f"suficiente={res.sufficient} motivo={res.abstain_reason} cobertura={res.term_coverage:.2f} estagios={res.stages}")
    for e in res.evidences:
        c_ = e.citation
        print(f"- score={e.score:.4f} lex#{e.lexical_rank} vec#{e.vector_rank} | {c_.get('processo')} | {c_.get('tipo')} | {c_.get('titulo')[:40]} | pag {e.page}")
        print("   ", e.text[:args.chars].replace("\n", " "))
    return 0


def cmd_ask(args) -> int:
    from app.container import build_container
    s = get_settings()
    c = build_container(s, gateway=None)
    st = c.ops.get(f"cli:{args.session}")
    turn = c.orchestrator.respond(args.question, st)
    c.ops.save(turn.state)
    r = turn.reply
    print(f"[{r.kind.value}] dominio={r.domain} intencao={r.intent} equipe={turn.handoff_team} etiquetas={turn.labels}")
    print(r.text or r.handoff_reason)
    if args.trace:
        _print(r.trace)
    return 0


def cmd_maintenance(args) -> int:
    from app.container import build_collection, build_store
    s = get_settings()
    svc = build_collection(s, build_store(s))
    _print({"stale": svc.refresh_staleness(), "conflitos": svc.detect_conflicts()})
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tjpa-piloto")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("ingest", help="ingere PDFs de docs/processos"); sp.add_argument("--dir"); sp.add_argument("--force", action="store_true")
    sp.add_argument("--only", nargs="*"); sp.set_defaults(fn=cmd_ingest)
    sp = sub.add_parser("stats"); sp.set_defaults(fn=cmd_stats)
    sp = sub.add_parser("docs", help="lista documentos"); sp.add_argument("--domain"); sp.add_argument("--state"); sp.add_argument("--process")
    sp.add_argument("--limit", type=int, default=50); sp.set_defaults(fn=cmd_docs)
    for name, st in (("approve", ReviewState.APPROVED), ("reject", ReviewState.REJECTED)):
        sp = sub.add_parser(name); sp.add_argument("doc_id", nargs="*"); sp.add_argument("--process"); sp.add_argument("--reviewer", required=True)
        sp.add_argument("--reason", required=True); sp.set_defaults(fn=lambda a, st=st: _review(a, st))
    sp = sub.add_parser("collect", help="coleta fontes institucionais e jurídicas autorizadas"); sp.add_argument("--domain", default="institucional")
    sp.add_argument("--url"); sp.set_defaults(fn=cmd_collect)
    sp = sub.add_parser("search", help="recuperação híbrida sem LLM"); sp.add_argument("query"); sp.add_argument("--domain", default="processual")
    sp.add_argument("--process"); sp.add_argument("--chars", type=int, default=220); sp.set_defaults(fn=cmd_search)
    sp = sub.add_parser("ask", help="pergunta ao orquestrador (usa o LLM configurado)"); sp.add_argument("question"); sp.add_argument("--session", default="dev")
    sp.add_argument("--trace", action="store_true"); sp.set_defaults(fn=cmd_ask)
    sp = sub.add_parser("maintenance", help="marca desatualizados e detecta divergências"); sp.set_defaults(fn=cmd_maintenance)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
