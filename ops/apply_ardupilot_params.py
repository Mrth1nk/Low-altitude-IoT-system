#!/usr/bin/env python3
"""Audit a parameter change set without changing ArduPilot parameters."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


class ChangeSetError(ValueError):
    pass


REQUIRED_CHANGE_FIELDS = (
    "name",
    "old",
    "new",
    "official_url",
    "reason",
    "restore_command",
)


def validate_change_set(changeset, *, vehicle=None, firmware=None):
    if not isinstance(changeset, dict):
        raise ChangeSetError("change set must be an object")
    for key in ("vehicle", "firmware", "changes"):
        if key not in changeset:
            raise ChangeSetError(f"change set missing {key}")
    if vehicle is not None and changeset["vehicle"] != vehicle:
        raise ChangeSetError(
            f"vehicle mismatch expected={vehicle} actual={changeset['vehicle']}"
        )
    if firmware is not None and changeset["firmware"] != firmware:
        raise ChangeSetError(
            f"firmware mismatch expected={firmware} actual={changeset['firmware']}"
        )
    if not isinstance(changeset["changes"], list):
        raise ChangeSetError("changes must be a list")
    names = set()
    for change in changeset["changes"]:
        if any(field not in change for field in REQUIRED_CHANGE_FIELDS):
            raise ChangeSetError("change is missing an audit field")
        name = str(change["name"]).strip().upper()
        if not name or name in names:
            raise ChangeSetError(f"duplicate or empty parameter name: {name}")
        names.add(name)
        if not str(change["official_url"]).startswith(("https://", "http://")):
            raise ChangeSetError(f"official_url missing for {name}")
        if not str(change["reason"]).strip():
            raise ChangeSetError(f"reason missing for {name}")
        if not str(change["restore_command"]).strip():
            raise ChangeSetError(f"restore command missing for {name}")
    return changeset


def apply_changes(
    connection,
    changeset,
    *,
    vehicle,
    firmware,
    execute=False,
):
    del connection, execute
    validate_change_set(changeset, vehicle=vehicle, firmware=firmware)
    return {
        "dry_run": True,
        "write_performed": False,
        "vehicle": vehicle,
        "firmware": firmware,
        "changes": len(changeset["changes"]),
        "message": "audit only; parameter writes are disabled in this project",
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--changes", required=True)
    parser.add_argument("--vehicle")
    parser.add_argument("--firmware")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="accepted for compatibility but remains audit-only",
    )
    args = parser.parse_args(argv)
    changeset = json.loads(Path(args.changes).read_text())
    validate_change_set(
        changeset,
        vehicle=args.vehicle,
        firmware=args.firmware,
    )
    result = apply_changes(
        None,
        changeset,
        vehicle=changeset["vehicle"],
        firmware=changeset["firmware"],
        execute=args.execute,
    )
    print(
        "DRY-RUN: "
        f"{result['changes']} change(s) for {result['vehicle']} / {result['firmware']}; "
        "no parameter writes performed"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
