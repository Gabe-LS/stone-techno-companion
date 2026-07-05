"""Module C -- source-agnostic signal timeline with ordered and timing-bound assertions.

A SignalRecorder is the assertion backbone shared by every notification test scenario.
Scenarios feed it heterogeneous signals -- FakePushService captures, WebSocket frames,
DB deltas, server-log greps -- each tagged with a source and a kind, and then assert on
the resulting timeline. Pure standard library: time, json, dataclasses. No external
dependencies and no network or filesystem access beyond `dump`.
"""

import json
import time
from dataclasses import dataclass, field, asdict


@dataclass
class Signal:
    """One recorded event on the timeline.

    t: time.monotonic()-comparable timestamp (or whatever `clock` returns).
    source: which part of the harness produced this signal (e.g. "fps", "ws:alice", "log").
    kind: the signal's type name (e.g. "push_sent", "message_acked", "badge_update").
    data: free-form payload for later inspection; not used by ordering/assertions.
    """

    t: float
    source: str
    kind: str
    data: dict = field(default_factory=dict)


class SignalRecorder:
    """Collects Signals from any number of sources and asserts on their combined timeline."""

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._signals: list[Signal] = []

    def record(
        self, source: str, kind: str, data: dict | None = None, t: float | None = None
    ) -> None:
        """Append a signal. `t` defaults to `clock()` if not given."""
        self._signals.append(
            Signal(
                t=t if t is not None else self._clock(),
                source=source,
                kind=kind,
                data=data or {},
            )
        )

    def timeline(self) -> list[Signal]:
        """All recorded signals, sorted by timestamp (ties keep insertion order)."""
        return sorted(self._signals, key=lambda s: s.t)

    def of_kind(self, kind: str) -> list[Signal]:
        """All signals of a given kind, in timeline order."""
        return [s for s in self.timeline() if s.kind == kind]

    def count(self, kind: str) -> int:
        """Number of signals of a given kind."""
        return len(self.of_kind(kind))

    def first(self, kind: str) -> Signal | None:
        """The earliest signal of a given kind, or None if it never occurred."""
        matches = self.of_kind(kind)
        return matches[0] if matches else None

    def clear(self) -> None:
        """Drop all recorded signals (call between scenarios)."""
        self._signals = []

    def dump(self, path: str) -> None:
        """Write the timeline to `path` as JSON, for test artifacts."""
        with open(path, "w") as f:
            json.dump([asdict(s) for s in self.timeline()], f, indent=2, default=str)

    def assert_sequence(self, kinds: list[str], *, strict: bool = False) -> None:
        """Assert that `kinds` appear in the timeline in the given order.

        strict=False: the kinds must occur in order but other signals (including
        repeats) may be interleaved between them (subsequence match).
        strict=True: the matched occurrences must be exactly consecutive in the
        timeline -- no other signal may fall between them.

        Raises AssertionError with the expected order and the actual timeline
        kinds on failure.
        """
        timeline = self.timeline()
        actual_kinds = [s.kind for s in timeline]

        indices: list[int] = []
        search_from = 0
        for kind in kinds:
            found_at = None
            for i in range(search_from, len(actual_kinds)):
                if actual_kinds[i] == kind:
                    found_at = i
                    break
            if found_at is None:
                raise AssertionError(
                    "assert_sequence failed: could not find kind {!r} after position {}\n"
                    "  expected order: {}\n"
                    "  actual timeline: {}".format(
                        kind, search_from, kinds, actual_kinds
                    )
                )
            indices.append(found_at)
            search_from = found_at + 1

        if strict:
            for a, b in zip(indices, indices[1:]):
                if b != a + 1:
                    raise AssertionError(
                        "assert_sequence(strict=True) failed: matched kinds are not consecutive\n"
                        "  expected order: {}\n"
                        "  actual timeline: {}\n"
                        "  matched positions: {}".format(kinds, actual_kinds, indices)
                    )

    def assert_within(
        self, before_kind: str, after_kind: str, max_seconds: float
    ) -> None:
        """Assert the first `after_kind` occurs strictly after the first `before_kind`,
        and within `max_seconds` of it.

        Raises AssertionError with the measured delta (or the missing kind) on failure.
        """
        before = self.first(before_kind)
        if before is None:
            raise AssertionError(
                "assert_within failed: no signal of kind {!r} was recorded".format(
                    before_kind
                )
            )

        after = self.first(after_kind)
        if after is None:
            raise AssertionError(
                "assert_within failed: no signal of kind {!r} was recorded".format(
                    after_kind
                )
            )

        delta = after.t - before.t
        if delta < 0:
            raise AssertionError(
                "assert_within failed: {!r} (t={}) occurred before {!r} (t={}), delta={:.3f}s".format(
                    after_kind, after.t, before_kind, before.t, delta
                )
            )
        if delta > max_seconds:
            raise AssertionError(
                "assert_within failed: {!r} occurred {:.3f}s after {!r}, exceeding max_seconds={:.3f}s".format(
                    after_kind, delta, before_kind, max_seconds
                )
            )

    def assert_absent(self, kind: str) -> None:
        """Assert that no signal of `kind` was ever recorded."""
        matches = self.of_kind(kind)
        if matches:
            raise AssertionError(
                "assert_absent failed: expected no signal of kind {!r}, found {}: {}".format(
                    kind, len(matches), [asdict(s) for s in matches]
                )
            )
