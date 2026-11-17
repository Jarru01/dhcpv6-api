"""Request/response models and input validation."""

from __future__ import annotations

import itertools
import re
from datetime import datetime
from enum import Enum
from ipaddress import IPv6Address, IPv6Network

from pydantic import BaseModel, ConfigDict, Field, field_validator

DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*\.?$")


def parse_pool(text: str) -> tuple[IPv6Address, IPv6Address]:
    """Parse a Kea pool ("start - end" or a prefix like "fd00::200/120") into its bounds."""
    text = text.strip()
    if "/" in text:
        network = IPv6Network(text, strict=True)
        return network[0], network[-1]
    parts = [p.strip() for p in text.split("-")]
    if len(parts) != 2:
        raise ValueError("pool must be 'start - end' or a prefix such as 'fd00::200/120'")
    start, end = IPv6Address(parts[0]), IPv6Address(parts[1])
    if start > end:
        raise ValueError("pool start address must not be greater than its end address")
    return start, end


class PoolIn(BaseModel):
    pool: str = Field(
        description="Address range 'start - end' or a prefix 'addr/len'",
        examples=["fd00::200 - fd00::2ff", "fd00::300/120"],
    )

    @field_validator("pool")
    @classmethod
    def _normalise(cls, value: str) -> str:
        try:
            if "/" in value:
                return str(IPv6Network(value.strip(), strict=True))
            start, end = parse_pool(value)
        except ValueError as exc:
            raise ValueError(f"invalid pool: {exc}") from exc
        return f"{start} - {end}"


class OptionName(str, Enum):
    dns_servers = "dns-servers"
    domain_search = "domain-search"
    sntp_servers = "sntp-servers"


class OptionValues(BaseModel):
    values: list[str] = Field(min_length=1, examples=[["2001:4860:4860::8888"]])


def normalise_option_values(name: OptionName, values: list[str]) -> list[str]:
    """Validate values for the given option; returns them in canonical form."""
    result = []
    for value in values:
        value = value.strip()
        if name is OptionName.domain_search:
            if not DOMAIN_RE.match(value):
                raise ValueError(f"'{value}' is not a valid domain name")
            result.append(value.lower())
        else:
            try:
                result.append(str(IPv6Address(value)))
            except ValueError:
                raise ValueError(f"'{value}' is not a valid IPv6 address") from None
    return result


class Timers(BaseModel):
    renew_timer: int | None = Field(None, ge=1, description="T1: when the client sends Renew (s)")
    rebind_timer: int | None = Field(None, ge=1, description="T2: when the client sends Rebind (s)")
    preferred_lifetime: int | None = Field(None, ge=1, description="Preferred lifetime (s)")
    valid_lifetime: int | None = Field(None, ge=1, description="Valid lifetime (s)")


TIMER_KEYS = {
    "renew_timer": "renew-timer",
    "rebind_timer": "rebind-timer",
    "preferred_lifetime": "preferred-lifetime",
    "valid_lifetime": "valid-lifetime",
}


def check_timer_order(timers: Timers) -> None:
    """Require renew <= rebind <= preferred <= valid (RFC 8415) for the timers that are set."""
    present = [(name, getattr(timers, name)) for name in TIMER_KEYS]
    present = [(name, value) for name, value in present if value is not None]
    for (a_name, a), (b_name, b) in itertools.pairwise(present):
        if a > b:
            raise ValueError(f"{a_name} ({a}) must not be greater than {b_name} ({b})")


class Subnet(BaseModel):
    id: int
    subnet: str
    interface: str | None = None
    pools: list[str]
    options: dict[str, str]


class Lease(BaseModel):
    model_config = ConfigDict(json_schema_extra={"description": "An active DHCPv6 lease"})

    address: str
    prefix_len: int
    lease_type: str
    duid: str
    iaid: int
    subnet_id: int
    hwaddr: str | None
    hostname: str | None
    valid_lifetime: int
    preferred_lifetime: int
    expires_at: datetime


class LeaseList(BaseModel):
    count: int
    leases: list[Lease]


class ServiceStatus(BaseModel):
    kea_dhcp6: str
    kea_uptime_s: int | None = None
    radvd: str
    subnets: int
    active_leases: int
