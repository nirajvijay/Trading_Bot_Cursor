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
        {
            ExecutionState.ENTRY_SUBMITTED,
            # The broker clearly and definitely refused the placement
            # (invalid quantity, insufficient margin). Never created, so
            # there is nothing to cancel and nothing to reconcile.
            ExecutionState.REJECTED,
            # An entry gate refused between writing intent and sending.
            ExecutionState.CANCELLED,
        }
    ),
    ExecutionState.ENTRY_SUBMITTED: frozenset(
        {ExecutionState.ENTERED, ExecutionState.REJECTED, ExecutionState.CANCELLED}
    ),
    ExecutionState.ENTERED: frozenset(
        {
            ExecutionState.PROTECTED,
            # Filled but deliberately never protected: abnormal slippage past
            # 1.5x the cap skips the stop and flattens immediately. Also the
            # path when a square-off trigger fires before the stop is live.
            ExecutionState.EXIT_SUBMITTED,
            # Reconciliation found the position already flat at the broker —
            # a human closed it while it sat unprotected.
            ExecutionState.CLOSED,
        }
    ),
    ExecutionState.PROTECTED: frozenset(
        {
            ExecutionState.TRAILING,
            ExecutionState.EXIT_SUBMITTED,
            # Reconciliation found the stop order gone from the broker. The
            # position genuinely is not protected any more, so the state must
            # say so and the stop gets re-placed — recording it as still
            # PROTECTED would make the unprotected count lie.
            ExecutionState.ENTERED,
            # The standing stop fired on its own (the passive exit path).
            ExecutionState.CLOSED,
        }
    ),
    ExecutionState.TRAILING: frozenset(
        {
            ExecutionState.EXIT_SUBMITTED,
            ExecutionState.ENTERED,
            ExecutionState.CLOSED,
        }
    ),
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
