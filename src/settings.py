"""Load /settings.json -- stop ids, direction filter, refresh cadence.

Deliberately NOT committed to git (see .gitignore: src/settings.json) so
the owner's home stop doesn't end up in a public repo. See
settings.example.json for the template/field reference, and AGENTS.md
"Departures logic & stops" for what each field means and how to find a
site id.
"""
import json

if False:
    from typing import Any

PATH = "/settings.json"


def load() -> "dict[str, Any]":
    try:
        with open(PATH) as f:
            return json.load(f)
    except OSError:
        raise OSError(
            "settings.json missing on device -- copy src/settings.example.json "
            "to src/settings.json, fill in your stop(s), then: "
            "mpremote connect COM3 fs cp src/settings.json :settings.json"
        )
