"""Host-side contract tests for SL's single-attempt HTTP adapter."""
import sys
from types import SimpleNamespace

import pytest

# The device supplies MicroPython's `requests` module; host adapter tests
# replace only its external `get` boundary before exercising the real module.
sys.modules.setdefault("requests", SimpleNamespace(get=None))

import sl


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
    # Removing the adapter's status guard would silently treat rate limiting
    # as a usable departure payload.
    response = FakeResponse(429, {"reason": "rate limited"})
    calls = []

    def get(*args, **kwargs):
        calls.append((args, kwargs))
        return response

    monkeypatch.setattr(sl.requests, "get", get)

    with pytest.raises(OSError, match="HTTP 429"):
        sl.fetch_departures(9192)

    assert response.closed
    assert len(calls) == 1


def test_departures_requires_top_level_departures(monkeypatch):
    # A 200 error-shaped response must not be mistaken for an empty timetable.
    response = FakeResponse(200, {"error": "unexpected"})
    monkeypatch.setattr(sl.requests, "get", lambda *a, **k: response)

    with pytest.raises(ValueError, match="departures"):
        sl.fetch_departures(9192)

    assert response.closed


def test_invalid_json_is_propagated_and_response_is_closed(monkeypatch):
    # Removing the finally block would leak a response whenever JSON decoding
    # fails on a malformed server response.
    response = FakeResponse(200, json_error=ValueError("invalid JSON"))
    monkeypatch.setattr(sl.requests, "get", lambda *a, **k: response)

    with pytest.raises(ValueError, match="invalid JSON"):
        sl.fetch_departures(9192)

    assert response.closed


def test_legitimate_empty_departures_is_success(monkeypatch):
    # Rejecting every empty list would turn a normal quiet-period response
    # into a stale-data failure.
    response = FakeResponse(200, {"departures": []})
    calls = []

    def get(*args, **kwargs):
        calls.append((args, kwargs))
        return response

    monkeypatch.setattr(sl.requests, "get", get)

    assert sl.fetch_departures(9192) == {"departures": []}
    assert len(calls) == 1
    assert response.closed
