"""The lossless collector observation contract shared by every T1/T2 method.

A collector method never collapses an unreadable/unavailable source into a scalar,
``None``, empty list, or boolean: it returns exactly one immutable observation whose state
the check maps deliberately (skip/fallback for unavailable, blocking unknown for
unreadable, criterion semantics for confirmed present/absent).
"""
from __future__ import annotations

from dataclasses import dataclass


class ObservationState:
    PRESENT = "present"
    ABSENT = "absent"
    UNREADABLE = "unreadable"
    UNAVAILABLE = "unavailable"


_STATES = frozenset({
    ObservationState.PRESENT, ObservationState.ABSENT,
    ObservationState.UNREADABLE, ObservationState.UNAVAILABLE,
})


@dataclass(frozen=True)
class CollectorObservation:
    """One T1/T2 fact: a closed state plus an immutable, validated payload.

    ``value`` is ``None`` for absent/unreadable/unavailable; for present it is an
    immutable validated primitive (str/int/float/bool), a tuple of them, or a tuple of
    frozen records. A ``present`` observation may legitimately carry an empty tuple — that
    is a confirmed empty answer, never an unreadable response.
    """

    state: str      # "present" | "absent" | "unreadable" | "unavailable"
    value: object = None
    reason: str = ""

    def __post_init__(self):
        if self.state not in _STATES:
            raise TypeError(f"unknown observation state: {self.state!r}")
        if self.state != ObservationState.PRESENT:
            if self.value is not None:
                raise TypeError("non-present observations carry no value")
        else:
            _require_immutable(self.value)
        if self.state in (ObservationState.PRESENT, ObservationState.ABSENT):
            if self.reason:
                raise TypeError("present/absent observations carry no reason")
        elif not self.reason:
            raise TypeError("unreadable/unavailable observations require a reason")

    @property
    def present(self) -> bool:
        return self.state == ObservationState.PRESENT

    @property
    def available(self) -> bool:
        """Whether the source was reachable at all (present or confirmed absent)."""
        return self.state in (ObservationState.PRESENT, ObservationState.ABSENT)


def _require_immutable(value) -> None:
    import dataclasses
    if value is None or isinstance(value, (str, int, float, bool)):
        return
    if isinstance(value, tuple):
        for item in value:
            _require_immutable(item)
        return
    if dataclasses.is_dataclass(value) and not isinstance(value, type) \
            and value.__dataclass_params__.frozen:
        return
    raise TypeError(f"observation value must be an immutable primitive/tuple: "
                    f"{type(value).__name__}")


def present(value) -> CollectorObservation:
    return CollectorObservation(ObservationState.PRESENT, value)


def absent() -> CollectorObservation:
    return CollectorObservation(ObservationState.ABSENT)


def unreadable(reason: str) -> CollectorObservation:
    return CollectorObservation(ObservationState.UNREADABLE, reason=reason)


def unavailable(reason: str) -> CollectorObservation:
    return CollectorObservation(ObservationState.UNAVAILABLE, reason=reason)
