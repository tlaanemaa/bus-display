"""Load/save the on-device /config.json.

An absent file just means "not configured yet" -- there is no bundled
default config, and Wi-Fi credentials never exist anywhere but this file
on the device (see CLAUDE.md).
"""
import json

PATH = "/config.json"


def load():
    try:
        with open(PATH) as f:
            return json.load(f)
    except OSError:
        return {}


def save(config):
    with open(PATH, "w") as f:
        json.dump(config, f)
