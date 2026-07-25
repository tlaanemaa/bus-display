"""Load/save the on-device /config.json.

An absent file just means "not configured yet" -- Wi-Fi credentials never
exist anywhere but this file on the device (see AGENTS.md). This is
deliberately Wi-Fi-only: stop ids/direction/refresh settings live in
/settings.json instead (see settings.py) -- both are runtime JSON, not
code, but kept as two separate files since only settings.json needs to be
gitignored (see AGENTS.md "Departures logic & stops").
"""
import json

if False:
    from typing import Any

PATH = "/config.json"


def load() -> "dict[str, Any]":
    try:
        with open(PATH) as f:
            return json.load(f)
    except OSError:
        return {}


def save(config: "dict[str, Any]") -> None:
    with open(PATH, "w") as f:
        json.dump(config, f)
