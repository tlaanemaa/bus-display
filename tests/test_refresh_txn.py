import pytest

import refresh_txn


def test_changed_refresh_invalidates_then_refreshes_then_commits():
    events = []
    refresh_txn.apply(
        {"frame": "new"},
        lambda state: events.append(("encode", state)) or b"new",
        lambda: events.append("invalidate"),
        lambda: events.append("refresh"),
        lambda raw: events.append(("commit", raw)),
    )
    assert events == [
        ("encode", {"frame": "new"}),
        "invalidate",
        "refresh",
        ("commit", b"new"),
    ]


def test_refresh_failure_never_commits_new_state():
    events = []

    def fail():
        events.append("refresh")
        raise OSError("panel")

    with pytest.raises(OSError, match="panel"):
        refresh_txn.apply(
            {},
            lambda state: b"new",
            lambda: events.append("invalidate"),
            fail,
            lambda raw: events.append(("commit", raw)),
        )
    assert events == ["invalidate", "refresh"]


def test_encode_failure_happens_before_invalidation():
    events = []

    def fail_encode(state):
        events.append("encode")
        raise ValueError("oversize")

    with pytest.raises(ValueError, match="oversize"):
        refresh_txn.apply(
            {},
            fail_encode,
            lambda: events.append("invalidate"),
            lambda: events.append("refresh"),
            lambda raw: events.append("commit"),
        )
    assert events == ["encode"]


def test_invalidation_failure_prevents_refresh_and_commit():
    events = []

    def fail_invalidate():
        events.append("invalidate")
        raise OSError("RTC invalidation")

    with pytest.raises(OSError, match="RTC invalidation"):
        refresh_txn.apply(
            {},
            lambda state: events.append("encode") or b"new",
            fail_invalidate,
            lambda: events.append("refresh"),
            lambda raw: events.append(("commit", raw)),
        )
    assert events == ["encode", "invalidate"]


def test_commit_failure_happens_only_after_successful_refresh():
    events = []

    def fail_commit(raw):
        events.append(("commit", raw))
        raise OSError("RTC commit")

    with pytest.raises(OSError, match="RTC commit"):
        refresh_txn.apply(
            {},
            lambda state: events.append("encode") or b"new",
            lambda: events.append("invalidate"),
            lambda: events.append("refresh"),
            fail_commit,
        )
    assert events == [
        "encode",
        "invalidate",
        "refresh",
        ("commit", b"new"),
    ]
