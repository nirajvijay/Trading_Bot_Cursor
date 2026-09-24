"""The loop's timing and failure policy, separated from the loop itself.

Both pieces here are pure functions and plain state, so drift arithmetic and
escalation thresholds are unit-testable without sleeping or running a real
tick.

**Tick interval: 1 second.** Justified against Kite's published limits (Quote
1 req/sec but up to 500 instruments per call, order placement 10/sec, 400
orders/minute) and, more importantly, against the tightest safety constraint in
the system: the window between a market order filling and its stop being
placed. A 1-second loop caps that window at about a second. This is not a
high-frequency system — continuation setups play out over minutes — so noticing
a fill a second later does not change trade quality, and a second is
imperceptible for a human clicking a button on the desk.

**Drift, not clock alignment.** After a tick, sleep whatever is left of the
second; if the tick overran, start the next one immediately. Ticks stay about a
second apart relative to each other, while the absolute clock time they land on
can slide later across the day. Chosen because staying clock-aligned would mean
*skipping* a tick outright whenever one overran, and skipping a check is worse
for us than running one late.

**Each step fails independently.** One step's bug must never block the others,
so every step is wrapped on its own rather than the whole tick being wrapped
once. A malformed trigger row breaking ingestion must not also skip protection
for a real, filled position on that tick: the least dangerous part of the loop
should never be able to silence the most dangerous part.

**Woken early by Kite, never faster than 0.5s.** Kite pushes order updates
(fills, a stop triggering, a cancel landing, a manual close) over the engine's
WebSocket. A push only ends the wait before the next tick early -- the tick
itself still reads REST truth exactly as always; the push's contents are never
used. MIN_TICK_GAP_SECONDS bounds a burst of pushes to at most two ticks a
second, i.e. about four positions/orders reads a second, well inside Kite's
10/second for those endpoints. With the WebSocket down, nothing ever wakes the
loop and it runs at the plain 1-second interval.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

TICK_INTERVAL_SECONDS = 1.0
MIN_TICK_GAP_SECONDS = 0.5

# Step names, used as keys for failure counting and escalation.
STEP_RECONCILE = "drive_open_orders"
STEP_PROTECTION = "ensure_protection"
STEP_INGEST = "ingest_triggers"
STEP_COMMANDS = "handle_commands"
STEP_SQUAREOFF = "squareoff"

# Consecutive-failure thresholds, tiered by how much real exposure builds up
# per second the step stays broken -- not one flat number for everything.
FAILURE_THRESHOLDS: Dict[str, int] = {
    # A freshly filled position with no stop at all. Every second counts, so
    # this has the least patience in the system.
    STEP_PROTECTION: 3,
    # We cannot protect a fill we do not know happened, so this delays
    # protection -- but it is one step removed from raw exposure.
    STEP_RECONCILE: 5,
    # A broken squareoff during a breach or EOD is serious, but the retry is
    # the mechanism, so it gets the same patience as reconciliation.
    STEP_SQUAREOFF: 5,
    # A broken ingest risks a missed trade, not open risk.
    STEP_INGEST: 10,
    STEP_COMMANDS: 10,
}

# Escalating these two suggests something is wrong with our ability to protect
# positions at all, so new entries get held back until a human looks. Existing
# positions keep being retried regardless: pausing only ever affects entries.
AUTO_PAUSE_ON_ESCALATION = (STEP_PROTECTION, STEP_RECONCILE)

DEFAULT_THRESHOLD = 10


def next_sleep_seconds(
    *,
    tick_started_at: float,
    tick_finished_at: float,
    interval: float = TICK_INTERVAL_SECONDS,
) -> float:
    """How long to wait before the next tick.

    Returns the remainder of the interval, or 0.0 when the tick overran — in
    which case the next tick starts immediately and the loop simply runs that
    much later for the rest of the session.
    """
    elapsed = max(0.0, float(tick_finished_at) - float(tick_started_at))
    return max(0.0, float(interval) - elapsed)


class LoopWake:
    """Lets a pushed Kite order update end the between-tick wait early.

    notify() may be called from any thread (KiteTicker's reactor thread in
    practice). The loop clears it at the *start* of each tick, before the REST
    reads, so a push landing mid-tick is not lost: it re-arms the next wait.
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def notify(self) -> None:
        self._event.set()

    def clear(self) -> None:
        self._event.clear()

    def wait(self, timeout: float) -> bool:
        return self._event.wait(timeout)


def wait_for_next_tick(
    *,
    sleep_for: float,
    tick_started_at: float,
    wait_for_wake: Callable[[float], bool],
    sleep: Callable[[float], None],
    monotonic: Callable[[], float],
    min_gap: float = MIN_TICK_GAP_SECONDS,
) -> bool:
    """Wait out the rest of the interval, or less if woken. Returns whether woken.

    A wake never starts the next tick sooner than ``min_gap`` after this tick
    started. An overrun tick (sleep_for == 0) starts the next one immediately,
    exactly as before.
    """
    if sleep_for <= 0:
        return False
    woke = bool(wait_for_wake(float(sleep_for)))
    if woke:
        remaining = float(min_gap) - (float(monotonic()) - float(tick_started_at))
        if remaining > 0:
            sleep(remaining)
    return woke


def accumulated_drift_seconds(
    *, started_at: float, now: float, ticks_completed: int, interval: float = TICK_INTERVAL_SECONDS
) -> float:
    """How far behind clock-aligned scheduling the loop has slid.

    Reported for observability only; nothing corrects for it, by design.
    Woken ticks come early, so on a busy day this reads low (floored at 0).
    """
    if ticks_completed <= 0:
        return 0.0
    expected = float(started_at) + (ticks_completed * float(interval))
    return max(0.0, float(now) - expected)


@dataclass
class StepFailureTracker:
    """Consecutive failures per step, and whether any has escalated."""

    thresholds: Dict[str, int] = field(default_factory=lambda: dict(FAILURE_THRESHOLDS))
    consecutive: Dict[str, int] = field(default_factory=dict)
    escalated: Dict[str, str] = field(default_factory=dict)
    last_error: Optional[str] = None

    def threshold_for(self, step: str) -> int:
        return self.thresholds.get(step, DEFAULT_THRESHOLD)

    def record_success(self, step: str) -> None:
        """A single success clears the streak. Failures must be consecutive to
        escalate — an intermittent blip is not a broken step."""
        self.consecutive.pop(step, None)
        self.escalated.pop(step, None)

    def record_failure(self, step: str, error: BaseException | str) -> bool:
        """Count a failure. Returns True if this one crossed the threshold."""
        message = str(error) or error.__class__.__name__
        self.last_error = f"{step}: {message}"
        count = self.consecutive.get(step, 0) + 1
        self.consecutive[step] = count
        threshold = self.threshold_for(step)
        if count >= threshold:
            self.escalated[step] = (
                f"{count} consecutive failures (threshold {threshold}): {message}"
            )
            return True
        return False

    @property
    def any_escalated(self) -> bool:
        return bool(self.escalated)

    def should_auto_pause(self) -> bool:
        """Only the two steps closest to real exposure hold back entries."""
        return any(step in self.escalated for step in AUTO_PAUSE_ON_ESCALATION)

    def escalated_steps(self) -> List[str]:
        return sorted(self.escalated)

    def snapshot(self) -> Dict[str, int]:
        return dict(self.consecutive)


def run_step(
    step: str,
    action: Callable[[], None],
    *,
    tracker: StepFailureTracker,
    on_escalate: Optional[Callable[[str, str], None]] = None,
) -> bool:
    """Run one tick step in isolation. Never raises.

    A single failure is logged and that step is skipped for this tick only,
    treated as likely transient. Crossing the step's threshold calls
    on_escalate so it can be made loudly visible rather than buried.
    """
    try:
        action()
    except Exception as exc:  # noqa: BLE001 - isolation is the whole point
        crossed = tracker.record_failure(step, exc)
        if crossed and on_escalate is not None:
            on_escalate(step, tracker.escalated.get(step, "escalated"))
        return False
    tracker.record_success(step)
    return True
