"""Pure wall-clock scheduling for one-cycle deep-sleep operation."""


def elapsed_due(
    now_s: float, previous_s: "float | None", interval_s: int,
) -> bool:
    """Whether an elapsed-time action is due, including invalid future state."""
    return (previous_s is None or previous_s > now_s
            or now_s - previous_s >= interval_s)


def request_boundary(now_s: float, interval_s: int = 60) -> int:
    """Current boundary if exactly on it, otherwise the next one."""
    now_s = int(now_s)
    remainder = now_s % interval_s
    return now_s if remainder == 0 else now_s + interval_s - remainder


def next_wake_delay_s(
    now_s: float, wake_advance_s: int, interval_s: int = 60,
) -> int:
    """Seconds from now to `advance` seconds before the next boundary.

    Always schedules a future minute. A zero advance therefore wakes exactly
    on the boundary; 3 wakes at HH:MM:57 for requests at the following :00.
    """
    now_s = int(now_s)
    advance = max(0, min(int(wake_advance_s), interval_s - 1))
    next_boundary = (now_s // interval_s + 1) * interval_s
    delay = next_boundary - advance - now_s
    if delay <= 0:
        delay += interval_s
    return delay
