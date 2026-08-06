import wake_schedule


def test_elapsed_due_handles_missing_expired_and_future_values():
    assert wake_schedule.elapsed_due(1000, None, 60) is True
    assert wake_schedule.elapsed_due(1000, 940, 60) is True
    assert wake_schedule.elapsed_due(1000, 941, 60) is False
    assert wake_schedule.elapsed_due(1000, 1001, 60) is True


def test_request_boundary_is_current_only_when_exact():
    assert wake_schedule.request_boundary(120) == 120
    assert wake_schedule.request_boundary(117) == 120
    assert wake_schedule.request_boundary(121) == 180


def test_three_second_advance_wakes_at_57():
    assert wake_schedule.next_wake_delay_s(120, 3) == 57
    assert wake_schedule.next_wake_delay_s(177, 3) == 60


def test_zero_advance_is_the_wake_at_boundary_fallback():
    assert wake_schedule.next_wake_delay_s(120, 0) == 60
    assert wake_schedule.next_wake_delay_s(177, 0) == 3


def test_advance_is_safely_clamped():
    assert wake_schedule.next_wake_delay_s(120, -10) == 60
    assert wake_schedule.next_wake_delay_s(120, 999) == 1
