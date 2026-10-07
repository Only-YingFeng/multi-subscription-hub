"""Build ZeroOmega schema 2 backups without reading or changing a browser.

Native format and restore behavior were checked against the public v3.5.2
source (FixedProfile fields, JSON export, Options.reset and local startup state):
https://github.com/zero-peak/ZeroOmega/blob/v3.5.2/omega-pac/src/profiles.coffee
https://github.com/zero-peak/ZeroOmega/blob/v3.5.2/omega-target/src/options.coffee
https://github.com/zero-peak/ZeroOmega/blob/v3.5.2/omega-web/src/omega/controllers/io.coffee
https://github.com/zero-peak/ZeroOmega/blob/v3.5.2/omega-web/src/omega/app.coffee

The backup contains options only. currentProfileName and sync credentials are
state fields and must not be invented as backup keys. A blank startup
profile preserves the browser's existing selection if that profile still exists;
native restore otherwise falls back to the built-in system profile.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


_DEFAULT_OPTIONS = {
    "schemaVersion": 2,
    "-enableQuickSwitch": False,
    "-refreshOnProfileChange": True,
    "-startupProfileName": "",
    "-quickSwitchProfiles": [],
    "-revertProxyChanges": True,
    "-confirmDeletion": True,
    "-showInspectMenu": True,
    "-addConditionsToBottom": False,
    "-showResultProfileOnActionBadgeText": False,
    "-showExternalProfile": True,
    "-downloadInterval": 1440,
}
_BYPASS_LIST = [
    {"conditionType": "BypassCondition", "pattern": "127.0.0.1"},
    {"conditionType": "BypassCondition", "pattern": "::1"},
    {"conditionType": "BypassCondition", "pattern": "localhost"},
]
_OLD_COUNTRY_PORTS = {"日本": 7898, "美国": 7899, "新加坡": 7900, "台湾": 7901, "香港": 7902}
_PROFILE_FIELDS = {"name", "profileType", "color", "fallbackProxy", "bypassList", "proxyDNS", "revision"}
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


def _registry_entries(entries: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    registry = []
    names, ports, ids = set(), set(), set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping) or type(entry.get("active")) is not bool:
            raise ValueError(f"Entry {index}: active must be a boolean")
        name, port, node_id = entry.get("profileName"), entry.get("port"), entry.get("id")
        if not isinstance(name, str) or not name.strip() or name.startswith("_"):
            raise ValueError(f"Entry {index}: profileName must be nonempty and visible")
        if name in {"direct", "system"} or any(ord(char) < 32 for char in name):
            raise ValueError(f"Entry {index}: profileName conflicts with native naming rules")
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError(f"Entry {index}: port must be an integer from 1 to 65535")
        if not isinstance(node_id, str) or not node_id:
            raise ValueError(f"Entry {index}: id must be a nonempty string")
        if name in names or port in ports or node_id in ids:
            raise ValueError(f"Entry {index}: duplicate registry profileName, port or id")
        names.add(name)
        ports.add(port)
        ids.add(node_id)
        registry.append(entry)
    return registry


def _native_revision(entry: Mapping[str, Any]) -> str:
    revision = entry.get("revision")
    if revision is not None:
        if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]+", revision):
            raise ValueError("Entry revision must be a native lowercase hexadecimal string")
        return revision
    # Repeatable initial revision. At most ten hex digits, so native timestamp
    # revisions produced by later UI edits compare as newer than this baseline.
    return format(int(sha256(entry["id"].encode("utf-8")).hexdigest()[:10], 16), "x")


def _is_old_country_profile(key: str, profile: Any) -> bool:
    """Identify only the five verified legacy local SOCKS5 definitions."""
    if not isinstance(profile, dict) or set(profile) != _PROFILE_FIELDS:
        return False
    name = profile.get("name")
    proxy = profile.get("fallbackProxy")
    return (
        name in _OLD_COUNTRY_PORTS
        and key == "+" + name
        and profile.get("profileType") == "FixedProfile"
        and isinstance(proxy, dict)
        and set(proxy) == {"scheme", "host", "port"}
        and proxy.get("scheme") == "socks5"
        and proxy.get("host") in _LOOPBACK_HOSTS
        and proxy.get("port") == _OLD_COUNTRY_PORTS[name]
        and profile.get("bypassList") == _BYPASS_LIST
        and profile.get("proxyDNS") is True
    )


def build_backup(
    entries: Iterable[Mapping[str, Any]], template_path: str | Path | None = None
) -> dict[str, Any]:
    """Return an importable backup; never write files or access browser storage.

    All registry entries remain FixedProfiles, including inactive entries whose
    unchanged ports lead to Mihomo reject listeners. This keeps a selected dead
    profile present during native restore. Unrelated template options/profiles
    are copied unchanged; conflicting existing profiles are rejected.
    """
    registry = _registry_entries(entries)
    if template_path is None:
        options = deepcopy(_DEFAULT_OPTIONS)
    else:
        options = json.loads(Path(template_path).read_text(encoding="utf-8-sig"))
        if not isinstance(options, dict) or options.get("schemaVersion") != 2:
            raise ValueError("Template must be a ZeroOmega schemaVersion 2 JSON object")

    removed = []
    for key, profile in list(options.items()):
        if key.startswith("+") and _is_old_country_profile(key, profile):
            removed.append(profile["name"])
            del options[key]

    # These are documented native options, not custom synchronization settings.
    options["-startupProfileName"] = ""
    options["-enableQuickSwitch"] = False
    quick_profiles = options.get("-quickSwitchProfiles", [])
    if not isinstance(quick_profiles, list):
        raise ValueError("Template quickSwitchProfiles must be an array")
    options["-quickSwitchProfiles"] = [name for name in quick_profiles if name not in removed]

    for entry in registry:
        name = entry["profileName"]
        key = "+" + name
        if key in options:
            old = options[key]
            same_endpoint = {"scheme": "socks5", "host": "127.0.0.1", "port": entry["port"]}
            if (not isinstance(old, dict) or set(old) != _PROFILE_FIELDS
                or old.get("profileType") != "FixedProfile"
                or old.get("fallbackProxy") != same_endpoint
                or old.get("bypassList") != _BYPASS_LIST or old.get("proxyDNS") is not True):
                raise ValueError("Generated profileName conflicts with a preserved template profile")
        options[key] = {
            "name": name,
            "profileType": "FixedProfile",
            "color": "#99ccee",
            "fallbackProxy": {"scheme": "socks5", "host": "127.0.0.1", "port": entry["port"]},
            "bypassList": deepcopy(_BYPASS_LIST),
            "proxyDNS": True,
            "revision": _native_revision(entry),
        }
    validate_backup(options, registry)
    return options


def validate_backup(
    options: Mapping[str, Any], entries: Iterable[Mapping[str, Any]]
) -> dict[str, Any]:
    """Validate the generated fields and native wrapper; raise on any mismatch."""
    registry = _registry_entries(entries)
    errors = []
    if not isinstance(options, Mapping):
        raise ValueError("Backup must be a JSON object")
    if type(options.get("schemaVersion")) is not int or options.get("schemaVersion") != 2:
        errors.append("schemaVersion must be 2")
    if options.get("-startupProfileName") != "":
        errors.append("startupProfileName must be empty")
    if options.get("-enableQuickSwitch") is not False:
        errors.append("Quick switch must remain disabled")
    if not isinstance(options.get("-quickSwitchProfiles"), list):
        errors.append("quickSwitchProfiles must be an array")

    profile_count = 0
    for key, profile in options.items():
        if not isinstance(key, str):
            errors.append("Backup keys must be strings")
        elif key.startswith("+"):
            profile_count += 1
            if not isinstance(profile, dict) or profile.get("name") != key[1:]:
                errors.append("A profile key does not match its name")

    for index, entry in enumerate(registry):
        profile = options.get("+" + entry["profileName"])
        if not isinstance(profile, dict):
            errors.append(f"Registry entry {index}: missing profile")
            continue
        expected_proxy = {"scheme": "socks5", "host": "127.0.0.1", "port": entry["port"]}
        if set(profile) != _PROFILE_FIELDS or profile.get("profileType") != "FixedProfile":
            errors.append(f"Registry entry {index}: unexpected profile type or fields")
        if profile.get("fallbackProxy") != expected_proxy:
            errors.append(f"Registry entry {index}: local SOCKS5 endpoint mismatch")
        if profile.get("bypassList") != _BYPASS_LIST or profile.get("proxyDNS") is not True:
            errors.append(f"Registry entry {index}: bypass or DNS mismatch")
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", str(profile.get("color", ""))):
            errors.append(f"Registry entry {index}: invalid color")
        if not re.fullmatch(r"[0-9a-f]+", str(profile.get("revision", ""))):
            errors.append(f"Registry entry {index}: invalid native revision")

    if errors:
        raise ValueError("ZeroOmega backup validation failed: " + "; ".join(errors))
    return {
        "profileCount": profile_count,
        "activeCount": sum(entry["active"] for entry in registry),
        "generatedCount": len(registry),
        "errors": [],
    }
