"""Máquina de estados do atendimento (CHW-01 a CHW-04). Etiquetas não controlam o bot."""

from __future__ import annotations

from enum import StrEnum


class HandoffState(StrEnum):
    BOT_ACTIVE = "bot_active"
    HANDOFF_REQUESTED = "handoff_requested"
    HANDOFF_IN_PROGRESS = "handoff_in_progress"
    HUMAN_ACTIVE = "human_active"
    HUMAN_CLOSED = "human_closed"
    AUTOMATION_RESUMED = "automation_resumed"


_ALLOWED: dict[HandoffState, frozenset[HandoffState]] = {
    HandoffState.BOT_ACTIVE: frozenset({HandoffState.HANDOFF_REQUESTED, HandoffState.HUMAN_ACTIVE}),
    HandoffState.HANDOFF_REQUESTED: frozenset({HandoffState.HANDOFF_IN_PROGRESS, HandoffState.HUMAN_ACTIVE}),
    HandoffState.HANDOFF_IN_PROGRESS: frozenset({HandoffState.HUMAN_ACTIVE, HandoffState.HANDOFF_REQUESTED}),
    HandoffState.HUMAN_ACTIVE: frozenset({HandoffState.HUMAN_CLOSED}),
    HandoffState.HUMAN_CLOSED: frozenset({HandoffState.AUTOMATION_RESUMED, HandoffState.HUMAN_ACTIVE}),
    HandoffState.AUTOMATION_RESUMED: frozenset({HandoffState.HANDOFF_REQUESTED, HandoffState.HUMAN_ACTIVE}),
}

_BOT_MAY_REPLY = frozenset({HandoffState.BOT_ACTIVE, HandoffState.AUTOMATION_RESUMED})


class InvalidTransition(ValueError):
    pass


def can_transition(current: HandoffState, target: HandoffState) -> bool:
    return target in _ALLOWED[current]


def ensure_transition(current: HandoffState, target: HandoffState) -> HandoffState:
    if not can_transition(current, target):
        raise InvalidTransition(f"transição inválida: {current.value} -> {target.value}")
    return target


def bot_may_reply(state: HandoffState) -> bool:
    """O bot só responde em `bot_active` e `automation_resumed` (CHW-02)."""
    return state in _BOT_MAY_REPLY
