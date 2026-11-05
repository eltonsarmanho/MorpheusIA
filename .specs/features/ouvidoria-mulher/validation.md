**Date**: 2026-11-04
**Spec**: `.specs/features/ouvidoria-mulher/spec.md`
**Diff range**: árvore de trabalho não comitada sobre `de95542` (`backend/`, `.specs/`, `docs/Mulher/`, `deploy/docker-compose.yml`)
**Verifier**: independent sub-agent (author ≠ verifier)
**Iteration**: 3 de 3 (1: FAIL, 7 sobreviventes e 1 defeito; 2: FAIL, M41, N10 e N05)

**Verdict**: PASS (iteration 3 of 3, offline development evidence only)
Basis: every OVM-01..OVM-26 has a test that asserts the spec-defined outcome, and no relevant mutant survives (31 applied, 31 killed). One residual design risk (typed "ajuda" and other greeting words exit the triage at text steps) is reported below for an owner decision; it is not a spec violation. This PASS is not homologation: all tests use fake Chatwoot and LLM.

# Validation: PASS — Ouvidoria da Mulher (OVM-01..OVM-26), final

Verifier: independent (author != verifier). Diff range: **working tree, not committed**. No commit, push, deploy, VM or Chatwoot touched. Date: 2026-11-04.

## 1. Suite (real)

`cd backend && .venv/bin/python -m pytest -q` -> **450 passed, 7 deselected, 1 warning** (434 in iteration 1, 448 in iteration 2). Deselected = `live`/`corpus` markers, not run.

## 2. Closure of earlier gaps (checked against code and tests)

| Gap | Fix verified | Evidence |
| --- | --- | --- |
| M41 "Perigo agora" without 190/180 (tautological test) | `texts(gw)[-2] == ovm.EMERGENCY_TEXT` | test_ouvidoria_mulher.py:421-426; M41 and P09 killed |
| N10 summary for a safe contact says "sem retorno" | new test asserts no "sem retorno", the regular summary, and the final notice with continuity | test_ouvidoria_mulher.py:525-531; N10 and P08 killed |
| N05 "sair" does not return to the menu | new test asserts the menu comes back (`last_opts(gw)[3] == OPT_ENTRY.title`) for "sair" and "menu" | test_ouvidoria_mulher.py:534-540; N05 killed |
| OVM-17 typed exit at text steps | `handler.py:263-264` excludes `CLOSE_HINT` and `MENU` from the text capture; `handler.py:277-284` clears the triage, sends LEFT_TEXT and returns to the menu | P01, P02, P03, P04 killed |
| M13, M14, M17, M28, M42, M46, N01-N03 | killed | below |

## 3. Discrimination sensor (isolated scratch, deleted)

Scratch copy of `backend/` (baseline 450 passed there, `docs/` and `data/` symlinked and unlinked afterwards). Whole suite per mutant. **31 applied, 31 killed, 0 survived.** One earlier mutant (N04) no longer applies because the guard text changed; P01/P02 replace it.

Killed: M13 flag ignored on the violence-text offer; M14 triage read with the flag off; M17 invalid check digits accepted; M28 skip stores None; M41/M42 "Perigo agora" without 190/180 or without marking perigo; M46 Encaminhar outside the summary; N01-N03 `no_return` rule variants; N05 typed exit without menu; N06/N07 danger step guard; N08/N09/N10 summary templates and contacts; N11 final notice wording; N12 labels lost on retry; N13 note without the "não responder" order; N14/N15 flag guards (actions, menu); N16 text-step guard limited to one step; N17 triage not erased on resolve; P01 typed "menu" captured as relato; P02 typed "sair" captured as relato; P03 exit without clearing the triage; P04 exit without LEFT_TEXT; P05 text-step capture ignoring `is_option_click`; P06 any non-exit text leaves the step; P07 relato discarded at the text step; P08 safe-contact summary wording; P09 button sends the wrong emergency text.

## 4. Per-AC status

| AC | Discriminating test (test_ouvidoria_mulher.py unless noted) | Status |
| --- | --- | --- |
| OVM-01 | :71, :79, :372, :408 | OK |
| OVM-02, 03 | :86, :94, :101 | OK |
| OVM-04..07 | :108, :118, :126, :135, :498 | OK |
| OVM-08, 09 | :144, :157, :457 | OK |
| OVM-10 | :172 ("Sim"), :421 (button) | OK |
| OVM-11, 12, 13, 14 | :184, :192, :209, :449, :218, :231, :484 | OK |
| OVM-15 | tests/unit/test_triage_mulher.py (constant-level) | OK |
| OVM-16 | :250, :262, :465, :525 | OK |
| OVM-17 | :269, :396, :534 | OK |
| OVM-18..22 | :295, :316, :324, :331, :344, :477, :491 | OK |
| OVM-23 | :353 | OK |
| OVM-24 | :362, :507, :517 | OK |
| OVM-25 | tests/unit/test_triage_mulher.py:101 | OK |
| OVM-26 | tests/integration/test_chatwoot_client_and_setup.py:131-160 | OK |

## 5. Safety and misleading-text review of the new paths

- Typed exit only fires when the whole message equals an exit or greeting word (`flow.parse`, `flow.py:262-282`). Sentences such as "quero sair de casa" or "preciso de ajuda" are stored as relato (probe: "preciso de ajuda" parses as free text).
- After an exit the system says nothing was saved (LEFT_TEXT) and the triage row is cleared, so it never implies forwarding.
- No new leak: audit entries stay empty (`mulher_saida`), logs carry only exception types, attributes are enumerations.
- Safe-contact path now tells the woman a reply will come by this WhatsApp only when she confirmed it is safe; every other contact answer (including unanswered and "Prefiro não dizer") gets the no-return wording and the note orders not to reply here.

## 6. Remaining gaps (none blocking)

1. **Design risk, owner decision:** at the relato and número steps, a message that is only a greeting-type word exits and discards the triage. Probe: "ajuda", "ajuda!", "oi", "tudo bem", "bom dia", "início" and "menu" all parse as the menu action. A woman who sends just "ajuda" as her relato loses her answers and is shown the menu (the menu text does not itself carry 190/180). Consider limiting the typed exit at text steps to "sair" and "menu", or sending the emergency text with LEFT_TEXT. Not a spec violation (OVM-17 names only "sair").
2. OVM-15 is verified at constant level (question texts), not at runtime; there is no code path that asks for the forbidden data.
3. "Menu" exit in the middle of a failed handoff and the orphan-triage-on-flag-off rollback were covered in earlier iterations (the flag-off case is now tested at :408).
4. Real validation still needs the rehearsal with the Ouvidoria and the DPO decision on retention.

## 7. Real tree

`git status --porcelain` before and after this re-verification is identical. The only file I wrote is this report. `.env.example`, `.specs/STATE.md`, `deploy/docker-compose.yml` and `docs/OPERACAO.md` were edited by the author, not by this verifier.


## Pós-veredito (autor, 2026-11-04)

O risco de desenho da iteração 3 foi tratado: nas etapas de texto só "sair" e "menu" saem; "ajuda", "oi" e saudações sozinhas viram relato; a mensagem de saída cita 190 e 180. Teste novo: `test_palavra_de_saudacao_sozinha_no_relato_e_guardada_e_a_saida_cita_190`. Suíte: 451 passando. Esta mudança de poucas linhas não passou por nova rodada do Verifier.
