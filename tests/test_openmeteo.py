"""Contract tests for the Open-Meteo network adapter."""
import importlib
import sys

import pytest


class Response:
    def __init__(self, status_code, payload=None, json_error=None):
        self.status_code = status_code
        self.payload = payload
        self.json_error = json_error
        self.closed = False

    def json(self):
        if self.json_error is not None:
            raise self.json_error
        return self.payload

    def close(self):
        self.closed = True


class Requests:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, timeout):
        self.calls.append((url, timeout))
        return self.responses.pop(0)


def load_openmeteo(monkeypatch, requests):
    monkeypatch.setitem(sys.modules, "requests", requests)
    monkeypatch.delitem(sys.modules, "openmeteo", raising=False)
    module = importlib.import_module("openmeteo")
    del sys.modules["openmeteo"]
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    return module


def test_valid_forecast_is_returned_and_response_is_closed(monkeypatch):
    """Catches leaking a usable forecast response."""
    payload = {"daily": {"time": ["2026-08-06"]}, "hourly": {"time": []}}
    response = Response(200, payload)
    requests = Requests([response])

    result = load_openmeteo(monkeypatch, requests).fetch_today(59.3, 18.1, retries=1)

    assert result == payload
    assert response.closed is True
    assert len(requests.calls) == 1


def test_non_success_status_retries_then_raises_oserror_and_closes(monkeypatch):
    """Catches accepting an HTTP error response as weather data."""
    first = Response(429, {})
    second = Response(429, {})
    requests = Requests([first, second])

    with pytest.raises(OSError):
        load_openmeteo(monkeypatch, requests).fetch_today(59.3, 18.1, retries=2)

    assert first.closed is True
    assert second.closed is True
    assert len(requests.calls) == 2


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"daily": [], "hourly": {}},
        {"daily": {}, "hourly": []},
        [],
    ],
)
def test_malformed_forecast_shape_raises_valueerror_and_closes(monkeypatch, payload):
    """Catches forwarding malformed envelope data to weather parsing."""
    response = Response(200, payload)
    requests = Requests([response])

    with pytest.raises(ValueError):
        load_openmeteo(monkeypatch, requests).fetch_today(59.3, 18.1, retries=1)

    assert response.closed is True
    assert len(requests.calls) == 1


def test_json_failure_retries_and_closes_each_response(monkeypatch):
    """Catches JSON errors escaping before the configured retry attempts."""
    first = Response(200, json_error=ValueError("invalid JSON"))
    second = Response(200, json_error=ValueError("invalid JSON"))
    requests = Requests([first, second])

    with pytest.raises(ValueError):
        load_openmeteo(monkeypatch, requests).fetch_today(59.3, 18.1, retries=2)

    assert first.closed is True
    assert second.closed is True
    assert len(requests.calls) == 2


def test_json_failure_does_not_log_response_text(monkeypatch, capsys):
    """Catches response text being leaked through an exception diagnostic."""
    marker = "RESPONSE-BODY-MUST-STAY-PRIVATE"
    response = Response(200, json_error=ValueError(marker))
    requests = Requests([response])

    with pytest.raises(ValueError):
        load_openmeteo(monkeypatch, requests).fetch_today(59.3, 18.1, retries=1)

    assert marker not in capsys.readouterr().out


def test_fake_adapter_import_is_not_retained_after_its_patch_ends():
    """Catches a fake requests dependency leaking into later test imports."""
    missing = object()
    original = sys.modules.pop("openmeteo", missing)
    try:
        with pytest.MonkeyPatch.context() as patch:
            load_openmeteo(patch, Requests([Response(200, {"daily": {}, "hourly": {}})]))
        assert sys.modules.get("openmeteo", missing) is original
    finally:
        sys.modules.pop("openmeteo", None)
        if original is not missing:
            sys.modules["openmeteo"] = original
