"""Order lifecycle state machine.

Isolates "what states can a position legally move between" from the mess of
inferring state from TradeRecord flags scattered across a 6000-line tick().
Callers ask for a transition; illegal ones raise instead of silently
corrupting engine state.
"""
from __future__ import annotations

from engine_types import ExecutionState, Position

ALLOWED_TRANSITIONS: dict[ExecutionState, frozenset[ExecutionState]] = {
    ExecutionState.PENDING_ENTRY: frozenset(
        {ExecutionState.ENTRY_SUBMITTED, ExecutionState.CANCELLED}
    ),
    ExecutionState.ENTRY_SUBMITTED: frozenset(
        {ExecutionState.ENTERED, ExecutionState.REJECTED, ExecutionState.CANCELLED}
    ),
    ExecutionState.ENTERED: frozenset({ExecutionState.PROTECTED}),
    ExecutionState.PROTECTED: frozenset(
        {ExecutionState.TRAILING, ExecutionState.EXIT_SUBMITTED}
    ),
    ExecutionState.TRAILING: frozenset({ExecutionState.EXIT_SUBMITTED}),
    ExecutionState.EXIT_SUBMITTED: frozenset({ExecutionState.CLOSED}),
    ExecutionState.CLOSED: frozenset(),
    ExecutionState.REJECTED: frozenset(),
    ExecutionState.CANCELLED: frozenset(),
}


class IllegalTransition(RuntimeError):
    def __init__(self, position: Position, target: ExecutionState) -> None:
        super().__init__(
            f"{position.trade_id}: cannot move {position.state} -> {target}"
        )


def transition(position: Position, target: ExecutionState) -> None:
    if target not in ALLOWED_TRANSITIONS[position.state]:
        raise IllegalTransition(position, target)
    position.state = target
