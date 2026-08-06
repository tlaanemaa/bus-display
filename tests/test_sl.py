"""Contract tests for the SL network adapter."""
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


def load_sl(monkeypatch, requests):
    monkeypatch.setitem(sys.modules, "requests", requests)
    monkeypatch.delitem(sys.modules, "sl", raising=False)
    module = importlib.import_module("sl")
    del sys.modules["sl"]
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    return module


def test_empty_departures_is_a_successful_closed_response(monkeypatch):
    """Catches rejecting a valid no-service response or leaking it."""
    response = Response(200, {"departures": []})
    requests = Requests([response])

    result = load_sl(monkeypatch, requests).fetch_departures(9192, retries=1)

    assert result == {"departures": []}
    assert response.closed is True
    assert len(requests.calls) == 1


def test_non_success_status_retries_then_raises_oserror_and_closes(monkeypatch):
    """Catches treating an HTTP error page as departure data."""
    first = Response(503, {"departures": []})
    second = Response(503, {"departures": []})
    requests = Requests([first, second])

    with pytest.raises(OSError):
        load_sl(monkeypatch, requests).fetch_departures(9192, retries=2)

    assert first.closed is True
    assert second.closed is True
    assert len(requests.calls) == 2


@pytest.mark.parametrize("payload", [{}, {"departures": {}}, []])
def test_malformed_departures_shape_raises_valueerror_and_closes(monkeypatch, payload):
    """Catches passing malformed API data on to the display parser."""
    response = Response(200, payload)
    requests = Requests([response])

    with pytest.raises(ValueError):
        load_sl(monkeypatch, requests).fetch_departures(9192, retries=1)

    assert response.closed is True
    assert len(requests.calls) == 1


def test_json_failure_retries_and_closes_each_response(monkeypatch):
    """Catches JSON errors escaping before the configured retry attempts."""
    first = Response(200, json_error=ValueError("invalid JSON"))
    second = Response(200, json_error=ValueError("invalid JSON"))
    requests = Requests([first, second])

    with pytest.raises(ValueError):
        load_sl(monkeypatch, requests).fetch_departures(9192, retries=2)

    assert first.closed is True
    assert second.closed is True
    assert len(requests.calls) == 2


def test_json_failure_does_not_log_response_text(monkeypatch, capsys):
    """Catches response text being leaked through an exception diagnostic."""
    marker = "RESPONSE-BODY-MUST-STAY-PRIVATE"
    response = Response(200, json_error=ValueError(marker))
    requests = Requests([response])

    with pytest.raises(ValueError):
        load_sl(monkeypatch, requests).fetch_departures(9192, retries=1)

    assert marker not in capsys.readouterr().out


def test_fake_adapter_import_is_not_retained_after_its_patch_ends():
    """Catches a fake requests dependency leaking into later test imports."""
    missing = object()
    original = sys.modules.pop("sl", missing)
    try:
        with pytest.MonkeyPatch.context() as patch:
            load_sl(patch, Requests([Response(200, {"departures": []})]))
        assert sys.modules.get("sl", missing) is original
    finally:
        sys.modules.pop("sl", None)
        if original is not missing:
            sys.modules["sl"] = original
