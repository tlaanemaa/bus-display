import retained


def _state():
    return {
        "v": 1,
        "frame": [[{
            "name": "Rosenmalm", "hero_main": "4", "hero_unit": "min",
            "badge_line": "474", "dest": "Slussen",
            "rows": [["440", "Slussen", "12 min"]], "stale": False,
        }], ["Son 19 jul 14:32"], {
            "date": "2026-07-19", "condition": "rain",
            "tmin": 12, "tmax": 20, "precip": 40,
        }],
        "last_full": 123,
    }


def test_round_trip_and_size():
    raw = retained.encode(_state())
    assert len(raw) < retained.MAX_BYTES
    assert retained.decode(raw) == _state()


def test_corruption_and_wrong_version_are_rejected():
    raw = bytearray(retained.encode(_state()))
    raw[-1] ^= 1
    assert retained.decode(bytes(raw)) is None
    state = _state()
    state["v"] = 2
    assert retained.decode(retained.encode(state)) is None


def test_oversize_state_is_rejected():
    state = _state()
    state["padding"] = "x" * retained.MAX_BYTES
    try:
        retained.encode(state)
    except ValueError:
        pass
    else:
        raise AssertionError("oversize state was accepted")


def test_json_canonicalizes_tuples_without_invalidating_state():
    state = _state()
    state["frame"][0][0]["rows"] = [("440", "Slussen", "12 min")]
    decoded = retained.decode(retained.encode(state))
    assert decoded is not None
    assert decoded["frame"][0][0]["rows"] == [["440", "Slussen", "12 min"]]
