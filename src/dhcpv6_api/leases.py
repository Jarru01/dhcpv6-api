"""Reads active leases from Kea's memfile lease database (CSV).

The memfile is append-only: every renewal or release appends a new row for the
same lease, and old rows are only removed when Kea's LFC compacts the file. So
the last row for an address wins, and a lease only counts as active if it has
not expired and is in the default state.
"""

from __future__ import annotations

import csv
import time
from datetime import datetime, timezone
from pathlib import Path

from .models import Lease

LEASE_TYPES = {"0": "IA_NA", "1": "IA_TA", "2": "IA_PD"}
STATE_DEFAULT = "0"


def _unescape(value: str) -> str:
    # Kea escapes commas in free-text fields (e.g. hostname) as "&#x2c"
    return value.replace("&#x2c", ",")


def _int(value: str | None, default: int = 0) -> int:
    try:
        return int(value) if value not in (None, "") else default
    except ValueError:
        return default


def read_active_leases(path: Path, now: float | None = None) -> list[Lease]:
    path = Path(path)
    if not path.exists():
        return []
    now = time.time() if now is None else now

    latest: dict[tuple[str, str], dict[str, str]] = {}
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            address = (row.get("address") or "").strip()
            if not address or address.startswith("#"):
                continue
            latest[(row.get("lease_type", "0"), address)] = row

    leases = []
    for row in latest.values():
        valid = _int(row.get("valid_lifetime"))
        expire = _int(row.get("expire"))
        if valid == 0 or expire <= now or row.get("state", STATE_DEFAULT) != STATE_DEFAULT:
            continue
        leases.append(
            Lease(
                address=row["address"],
                prefix_len=_int(row.get("prefix_len"), 128),
                lease_type=LEASE_TYPES.get(row.get("lease_type", "0"), "unknown"),
                duid=row.get("duid", ""),
                iaid=_int(row.get("iaid")),
                subnet_id=_int(row.get("subnet_id")),
                hwaddr=row.get("hwaddr") or None,
                hostname=_unescape(row.get("hostname", "")) or None,
                valid_lifetime=valid,
                preferred_lifetime=_int(row.get("pref_lifetime")),
                expires_at=datetime.fromtimestamp(expire, tz=timezone.utc),
            )
        )
    leases.sort(key=lambda lease: lease.address)
    return leases
