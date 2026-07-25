import copy

import pytest

import retained
import settings


def state(fingerprint="a1b2c3d4"):
    return {
        "v": retained.RETAINED_VERSION,
        "render_rev": retained.RENDER_REVISION,
        "settings": fingerprint,
        "frame": {
            "sections": [{
                "stop_key": "9192:2", "name": "Slussen",
                "hero_main": "4", "hero_unit": "min",
                "badge_line": "474", "dest": "Hemmesta",
                "rows": [["440", "Slussen", "12 min"]],
                "stale": False,
            }],
            "footer": ["Lor 25 jul 14:32"],
            "status": {"kind": "none"},
        },
        "last_full": 123,
        "weather": None,
        "weather_time": None,
        "last_ntp": 120,
    }


@pytest.fixture
def valid_cfg():
    return settings.validate({
        "stops": [
            {"name": "Slussen", "site_id": 9192},
            {"name": "Londonviadukten", "site_id": 1321},
        ],
        "direction_code": 2,
        "departures_per_stop": 3,
        "full_refresh_interval_min": 60,
        "weather": {
            "enabled": True,
            "latitude": 59.33,
            "longitude": 18.06,
            "pull_interval_min": 30,
            "max_age_min": 180,
        },
    })


def test_round_trip_and_size():
    raw = retained.encode(state())
    assert len(raw) < retained.MAX_BYTES
    assert retained.decode(raw, "a1b2c3d4", ["9192:2"]) == state()


def test_corruption_and_wrong_version_are_rejected():
    raw = bytearray(retained.encode(state()))
    raw[-1] ^= 1
    assert retained.decode(bytes(raw), "a1b2c3d4", ["9192:2"]) is None
    incompatible = state()
    incompatible["v"] = retained.RETAINED_VERSION - 1
    assert retained.decode(
        retained.encode(incompatible), "a1b2c3d4", ["9192:2"],
    ) is None


def test_oversize_state_is_rejected():
    oversized = state()
    oversized["padding"] = "x" * retained.MAX_BYTES
    with pytest.raises(ValueError):
        retained.encode(oversized)


def test_json_canonicalizes_tuples_without_invalidating_state():
    retained_state = state()
    retained_state["frame"]["sections"][0]["rows"] = [
        ("440", "Slussen", "12 min"),
    ]
    decoded = retained.decode(
        retained.encode(retained_state), "a1b2c3d4", ["9192:2"],
    )
    assert decoded is not None
    assert decoded["frame"]["sections"][0]["rows"] == [
        ["440", "Slussen", "12 min"],
    ]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["frame"].pop("status"),
        lambda value: value["frame"].update(status={"kind": "invalid"}),
        lambda value: value["frame"]["sections"][0].update(
            rows=[["440", "Slussen"]],
        ),
        lambda value: value.update(last_full="123"),
        lambda value: value.update(weather_time=1.5),
        lambda value: value.update(last_ntp=True),
        lambda value: value.update(render_rev=retained.RENDER_REVISION + 1),
        lambda value: value.update(render_rev=True),
    ],
    ids=[
        "missing-frame-key",
        "invalid-status-kind",
        "wrong-row-length",
        "string-timestamp",
        "float-timestamp",
        "boolean-timestamp",
        "wrong-renderer-revision",
        "boolean-renderer-revision",
    ],
)
def test_decode_rejects_invalid_nested_state(mutate):
    invalid = state()
    mutate(invalid)
    assert retained.decode(
        retained.encode(invalid), "a1b2c3d4", ["9192:2"],
    ) is None


def test_decode_rejects_wrong_settings_fingerprint():
    assert retained.decode(
        retained.encode(state()), "ffffffff", ["9192:2"],
    ) is None


@pytest.mark.parametrize("expected_stop_keys", [[], ["1321:2"], ["9192:2", "1321:2"]])
def test_decode_rejects_section_count_or_identity_mismatch(expected_stop_keys):
    assert retained.decode(
        retained.encode(state()), "a1b2c3d4", expected_stop_keys,
    ) is None


def test_settings_fingerprint_changes_with_rendered_identity(valid_cfg):
    original = retained.settings_fingerprint(valid_cfg)
    changed = copy.deepcopy(valid_cfg)
    changed["stops"][0]["name"] = "Other label"
    assert retained.settings_fingerprint(changed) != original


def test_settings_fingerprint_ignores_no_runtime_objects(valid_cfg):
    assert (
        retained.settings_fingerprint(valid_cfg)
        == retained.settings_fingerprint(valid_cfg)
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda cfg: cfg["stops"].reverse(),
        lambda cfg: cfg["stops"][0].update(site_id=1),
        lambda cfg: cfg.update(direction_code=1),
        lambda cfg: cfg.update(departures_per_stop=2),
        lambda cfg: cfg.update(full_refresh_interval_min=30),
        lambda cfg: cfg["weather"].update(latitude=60.0),
        lambda cfg: cfg["weather"].update(longitude=19.0),
        lambda cfg: cfg["weather"].update(pull_interval_min=15),
        lambda cfg: cfg["weather"].update(max_age_min=90),
        lambda cfg: cfg.update(weather=None),
    ],
    ids=[
        "ordered-stops",
        "site-id",
        "direction",
        "departure-count",
        "full-refresh",
        "weather-latitude",
        "weather-longitude",
        "weather-pull-interval",
        "weather-max-age",
        "weather-disabled",
    ],
)
def test_settings_fingerprint_covers_compatibility_inputs(valid_cfg, mutate):
    original = retained.settings_fingerprint(valid_cfg)
    changed = copy.deepcopy(valid_cfg)
    mutate(changed)
    assert retained.settings_fingerprint(changed) != original
