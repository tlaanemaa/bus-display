"""Host-side contract tests for Open-Meteo's single-attempt HTTP adapter."""
import sys
from types import SimpleNamespace

import pytest

# The device supplies MicroPython's `requests` module; host adapter tests
# replace only its external `get` boundary before exercising the real module.
sys.modules.setdefault("requests", SimpleNamespace(get=None))

import openmeteo


class FakeResponse:
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


def test_non_2xx_is_failure_and_response_is_closed(monkeypatch):
    # Removing the adapter's status guard would treat a server failure as a
    # forecast payload and hide the weather error state.
    response = FakeResponse(503, {"reason": "unavailable"})
    calls = []

    def get(*args, **kwargs):
        calls.append((args, kwargs))
        return response

    monkeypatch.setattr(openmeteo.requests, "get", get)

    with pytest.raises(OSError, match="HTTP 503"):
        openmeteo.fetch_today(59.33, 18.06)

    assert response.closed
    assert len(calls) == 1


def test_weather_requires_top_level_daily_and_hourly_dicts(monkeypatch):
    # A 200 error-shaped payload must not flow into weather parsing as data.
    response = FakeResponse(200, {"daily": []})
    monkeypatch.setattr(openmeteo.requests, "get", lambda *a, **k: response)

    with pytest.raises(ValueError, match="daily/hourly"):
        openmeteo.fetch_today(59.33, 18.06)

    assert response.closed


def test_invalid_json_is_propagated_and_response_is_closed(monkeypatch):
    # Removing the finally block would leak the response on a JSON decoder
    # error, even though that failure is handled by main's weather fallback.
    response = FakeResponse(200, json_error=ValueError("invalid JSON"))
    monkeypatch.setattr(openmeteo.requests, "get", lambda *a, **k: response)

    with pytest.raises(ValueError, match="invalid JSON"):
        openmeteo.fetch_today(59.33, 18.06)

    assert response.closed


def test_valid_daily_and_hourly_payload_is_success_with_one_request(monkeypatch):
    payload = {"daily": {}, "hourly": {}}
    response = FakeResponse(200, payload)
    calls = []

    def get(*args, **kwargs):
        calls.append((args, kwargs))
        return response

    monkeypatch.setattr(openmeteo.requests, "get", get)

    assert openmeteo.fetch_today(59.33, 18.06) == payload
    assert len(calls) == 1
    assert response.closed
