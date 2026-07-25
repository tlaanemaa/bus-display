"""Enforce safe retained-state ordering around a panel refresh."""

if False:
    from typing import Callable
    from models import RetainedState


def apply(
    state: "RetainedState",
    encode: "Callable[[RetainedState], bytes]",
    invalidate: "Callable[[], None]",
    refresh: "Callable[[], None]",
    commit: "Callable[[bytes], None]",
) -> None:
    encoded_state = encode(state)
    invalidate()
    refresh()
    commit(encoded_state)
