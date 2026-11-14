"""
rules.py

Loads maica_rules.json -- how MAICA recognises each AI app (see that file's
"_about") -- once, merging in an override from the config folder if there is
one: a rules.json there replaces the bundled sections it contains, so a fix
for an app that changed its UI is a file drop, not a reinstall.
"""

import json
import os

from app_paths import bundled, config_dir

_cache = None


def load(force: bool = False) -> dict:
    global _cache
    if _cache is not None and not force:
        return _cache
    with open(bundled("maica_rules.json"), encoding="utf-8") as f:
        rules = json.load(f)
    override = os.path.join(config_dir(), "rules.json")
    if os.path.exists(override):
        try:
            with open(override, encoding="utf-8") as f:
                extra = json.load(f)
            if int(extra.get("version", 0)) >= int(rules.get("version", 0)):
                for k, v in extra.items():
                    if k == "apps" and isinstance(v, dict):
                        rules["apps"].update(v)
                    else:
                        rules[k] = v
        except Exception as e:
            print(f"[rules] ignoring {override}: {e}")
    _cache = rules
    return rules


def app_info(app_id: str) -> dict:
    return load()["apps"].get(app_id, {"name": app_id, "color": "A0A0A0"})


def as_json_bytes() -> bytes:
    return json.dumps(load()).encode("utf-8")
