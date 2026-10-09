"""Executor da avaliação (eval-harness): recuperação sem LLM e, opcionalmente, resposta ponta a ponta.

Uso:
  python -m evals.run_eval --mode retrieval            # recall@k, MRR, latência (sem LLM, sem custo)
  python -m evals.run_eval --mode e2e [--judge]        # orquestrador completo com MariTalk
Gera data/reports/eval-<modo>-<data>.json e imprime o resumo.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from app.config import get_settings
from app.container import build_container
from app.domain.models import KnowledgeDomain, ResponseKind
from app.infrastructure.sqlite.knowledge_store import fold
from app.infrastructure.sqlite.operational_store import ConversationState

SETS = {"dev": "questions.yaml", "heldout": "heldout.yaml", "heldout2": "heldout2.yaml"}


def load_questions(which: str = "dev") -> list[dict]:
    return yaml.safe_load(Path(__file__).with_name(SETS[which]).read_text(encoding="utf-8"))["questions"]


def _contains(haystack: str, needle: str) -> bool:
    return fold(needle) in fold(haystack)


def retrieval_eval(c, questions: list[dict], k: int) -> dict:
    rows, lat = [], []
    for q in questions:
        if not (q.get("gold_all") or q.get("gold_any")):
            continue
        t0 = time.perf_counter()
        res = c.retriever.retrieve(q["q"], KnowledgeDomain.PROCESSUAL, process_number=q.get("process"))
        lat.append((time.perf_counter() - t0) * 1000)
        texts = [e.text for e in res.evidences[:k]]
        blob = "\n".join(texts)
        if q.get("gold_all"):
            hit = all(_contains(blob, g) for g in q["gold_all"])
        else:
            hit = any(_contains(blob, g) for g in q["gold_any"])
        rank = None
        golds = q.get("gold_all") or q.get("gold_any")
        for i, t in enumerate(texts, 1):
            if any(_contains(t, g) for g in golds):
                rank = i
                break
        rows.append({"id": q["id"], "cat": q["cat"], "hit": hit, "first_rank": rank, "sufficient": res.sufficient})
    n = len(rows)
    return {
        "k": k, "n": n, "recall_at_k": round(sum(r["hit"] for r in rows) / n, 3) if n else None,
        "mrr": round(sum((1 / r["first_rank"]) if r["first_rank"] else 0 for r in rows) / n, 3) if n else None,
        "latency_ms_p50": round(statistics.median(lat), 1) if lat else None,
        "latency_ms_p95": round(sorted(lat)[int(0.95 * (len(lat) - 1))], 1) if lat else None,
        "failures": [r["id"] for r in rows if not r["hit"]], "rows": rows,
    }


def _check(q: dict, kind: str, text: str, reason: str | None) -> list[str]:
    problems = []
    exp = q["expect"]
    if exp != "any" and kind != exp:
        problems.append(f"esperado {exp}, obtido {kind}")
    if q.get("reason") and reason != q["reason"]:
        problems.append(f"motivo esperado {q['reason']}, obtido {reason}")
    for s in q.get("must_contain", []):
        if not _contains(text, s):
            problems.append(f"faltou '{s}'")
    if kind == "answer":
        for s in q.get("must_contain_if_answer", []):
            if not _contains(text, s):
                problems.append(f"faltou '{s}'")
    for s in q.get("must_not_contain", []):
        if _contains(text, s):
            problems.append(f"não deveria conter '{s}'")
    return problems


def e2e_eval(c, questions: list[dict], judge: bool) -> dict:
    rows = []
    for q in questions:
        st = ConversationState(f"eval:{q['id']}")
        t0 = time.perf_counter()
        error = None
        try:
            turn = c.orchestrator.respond(q["q"], st)
            kind, text = turn.reply.kind.value, turn.reply.text
            reason, trace, cites = turn.reply.abstain_reason, turn.reply.trace, len(turn.reply.citations)
        except Exception as exc:  # noqa: BLE001
            kind, text, reason, trace, cites, error = "error", "", None, {}, 0, f"{type(exc).__name__}: {exc}"
        ms = (time.perf_counter() - t0) * 1000
        problems = _check(q, kind, text, reason)
        row = {
            "id": q["id"], "cat": q["cat"], "kind": kind, "ok": not problems and error is None, "problems": problems, "latency_ms": round(ms),
            "citations": cites, "grounding_ok": (trace.get("grounding") or {}).get("ok"), "reason": reason, "error": error,
            "text": text,
        }
        if judge and kind == "answer":
            row["judge"] = llm_judge(c, q["q"], text)
        rows.append(row)
    answered = [r for r in rows if r["kind"] == "answer"]
    should_abstain = [r for r, q in zip(rows, questions) if q["expect"] == "abstain"]
    should_answer = [r for r, q in zip(rows, questions) if q["expect"] == "answer"]
    lat = [r["latency_ms"] for r in rows]
    summary = {
        "n": len(rows), "pass_rate": round(sum(r["ok"] for r in rows) / len(rows), 3),
        "abstention_correct": round(sum(r["kind"] in ("abstain", "handoff") for r in should_abstain) / max(1, len(should_abstain)), 3),
        "wrong_abstention_on_answerable": round(sum(r["kind"] != "answer" for r in should_answer) / max(1, len(should_answer)), 3),
        "citation_rate_on_answers": round(sum(r["citations"] > 0 for r in answered) / max(1, len(answered)), 3),
        "grounding_pass_rate_on_answers": round(sum(bool(r["grounding_ok"]) for r in answered) / max(1, len(answered)), 3),
        "error_rate": round(sum(r["error"] is not None or r["kind"] == "error" for r in rows) / len(rows), 3),
        "latency_ms_p50": statistics.median(lat), "latency_ms_p95": sorted(lat)[int(0.95 * (len(lat) - 1))],
        "failures": [(r["id"], r["problems"] or r["error"]) for r in rows if not r["ok"]],
    }
    if judge:
        js = [r["judge"] for r in answered if r.get("judge") and r["judge"].get("faithful") is not None]
        summary["judge_faithful_rate"] = round(sum(j["faithful"] for j in js) / max(1, len(js)), 3) if js else None
        summary["judge_note"] = "estimativa por LLM-juiz (mesma família do gerador); não substitui revisão humana"
    return {"summary": summary, "rows": rows}


def llm_judge(c, question: str, answer: str) -> dict:
    if c.llm is None:
        return {"faithful": None}
    prompt = ("Você é um avaliador. Dada a PERGUNTA e a RESPOSTA (que contém 'Fontes:'), diga se toda afirmação factual da resposta tem lastro "
              "nas fontes citadas e se a resposta evita inventar. Responda só JSON: {\"faithful\": true|false, \"motivo\": \"...\"}\n\n"
              f"PERGUNTA: {question}\n\nRESPOSTA:\n{answer}")
    try:
        raw = c.llm.generate(system="Responda somente com JSON válido.", user=prompt)
        s = raw[raw.find("{"): raw.rfind("}") + 1]
        return json.loads(s)
    except Exception as exc:  # noqa: BLE001
        return {"faithful": None, "error": type(exc).__name__}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["retrieval", "e2e"], default="retrieval")
    p.add_argument("--k", type=int, default=6)
    p.add_argument("--judge", action="store_true")
    p.add_argument("--only", nargs="*")
    p.add_argument("--set", choices=list(SETS), default="dev")
    args = p.parse_args()
    s = get_settings()
    c = build_container(s, gateway=None, llm=None if args.mode == "retrieval" else None)
    qs = [q for q in load_questions(args.set) if not args.only or q["id"] in args.only]
    out = retrieval_eval(c, qs, args.k) if args.mode == "retrieval" else e2e_eval(c, qs, args.judge)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    path = s.data_dir / "reports" / f"eval-{args.set}-{args.mode}-{stamp}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = out if args.mode == "retrieval" else out["summary"]
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, ensure_ascii=False, indent=2))
    print("relatório:", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
