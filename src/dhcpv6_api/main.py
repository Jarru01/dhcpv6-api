"""FastAPI application: REST endpoints for a Kea DHCPv6 server.

Run with:  uvicorn --factory dhcpv6_api.main:create_app
"""

from __future__ import annotations

import secrets
import shutil
import subprocess
from ipaddress import IPv6Network
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Path, Query, Request, Security
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader

from . import __version__
from .kea import (
    ConfigFile,
    ConfigManager,
    KeaControl,
    KeaError,
    SubnetNotFound,
    get_subnet,
    options_to_dict,
    remove_option,
    set_option,
)
from .leases import read_active_leases
from .models import (
    TIMER_KEYS,
    LeaseList,
    OptionName,
    OptionValues,
    PoolIn,
    ServiceStatus,
    Subnet,
    Timers,
    check_timer_order,
    normalise_option_values,
    parse_pool,
)
from .settings import Settings

DESCRIPTION = """
Manage a [Kea](https://www.isc.org/kea/) DHCPv6 server over HTTP: address pools,
DHCPv6 options, lease timers and active leases.

Every change is validated by Kea (`config-test`) before it is written, then
applied with `config-reload`; if the reload fails the previous config is restored.

All `/api/v1` endpoints require the `X-API-Key` header.
"""

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(request: Request, key: str | None = Security(api_key_header)) -> None:
    expected = request.app.state.settings.api_key.get_secret_value()
    if key is None or not secrets.compare_digest(key.encode(), expected.encode()):
        raise HTTPException(401, "Missing or invalid API key", headers={"WWW-Authenticate": "API-Key"})


def get_manager(request: Request) -> ConfigManager:
    return request.app.state.config


Manager = Annotated[ConfigManager, Depends(get_manager)]
SubnetId = Annotated[int, Path(ge=1, description="Kea subnet id ('id' in subnet6)")]
PoolIndex = Annotated[int, Path(ge=0, description="0-based position in the subnet's pool list")]

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])


def _invalid(message: str) -> HTTPException:
    return HTTPException(422, message)


def _subnet_view(subnet: dict[str, Any]) -> Subnet:
    return Subnet(
        id=subnet["id"],
        subnet=subnet["subnet"],
        interface=subnet.get("interface"),
        pools=[p.get("pool", "") for p in subnet.get("pools", [])],
        options=options_to_dict(subnet.get("option-data", [])),
    )


def _systemd_state(unit: str) -> str:
    if shutil.which("systemctl") is None:
        return "unknown"
    try:
        result = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    return result.stdout.strip() or "unknown"


# --- Status & config ---------------------------------------------------------


@router.get("/status", tags=["Server"], response_model=ServiceStatus, summary="Service health and counters")
def get_status(request: Request, manager: Manager) -> ServiceStatus:
    try:
        info = manager.kea.status()
        kea_state, uptime = "active", info.get("uptime")
    except KeaError:
        kea_state, uptime = "unreachable", None
    config = manager.read()
    leases = read_active_leases(request.app.state.settings.kea_leases_file)
    return ServiceStatus(
        kea_dhcp6=kea_state,
        kea_uptime_s=uptime,
        radvd=_systemd_state("radvd"),
        subnets=len(config["Dhcp6"].get("subnet6", [])),
        active_leases=len(leases),
    )


@router.get("/config", tags=["Server"], summary="The full Dhcp6 configuration (read-only)")
def get_config(manager: Manager) -> dict[str, Any]:
    return manager.read()["Dhcp6"]


@router.post("/reload", tags=["Server"], summary="Make Kea re-read its config file")
def reload_config(manager: Manager) -> dict[str, Any]:
    return {"message": "Configuration reloaded", "kea": manager.reload()}


# --- Subnets & pools ---------------------------------------------------------


@router.get("/subnets", tags=["Subnets & pools"], response_model=list[Subnet], summary="List subnets")
def list_subnets(manager: Manager) -> list[Subnet]:
    return [_subnet_view(s) for s in manager.read()["Dhcp6"].get("subnet6", [])]


@router.get("/subnets/{subnet_id}", tags=["Subnets & pools"], response_model=Subnet, summary="Get one subnet")
def get_subnet_detail(subnet_id: SubnetId, manager: Manager) -> Subnet:
    return _subnet_view(get_subnet(manager.read(), subnet_id))


def _check_pool(subnet: dict[str, Any], pool: str, skip_index: int | None = None) -> None:
    network = IPv6Network(subnet["subnet"])
    start, end = parse_pool(pool)
    if start not in network or end not in network:
        raise _invalid(f"Pool {pool} is not inside subnet {network}")
    for index, existing in enumerate(subnet.get("pools", [])):
        if index == skip_index:
            continue
        try:
            other_start, other_end = parse_pool(existing.get("pool", ""))
        except ValueError:
            continue
        if start <= other_end and other_start <= end:
            raise HTTPException(409, f"Pool overlaps existing pool {existing['pool']} (index {index})")


@router.get(
    "/subnets/{subnet_id}/pools", tags=["Subnets & pools"], response_model=list[str], summary="List pools of a subnet"
)
def list_pools(subnet_id: SubnetId, manager: Manager) -> list[str]:
    return _subnet_view(get_subnet(manager.read(), subnet_id)).pools


@router.post("/subnets/{subnet_id}/pools", tags=["Subnets & pools"], status_code=201, summary="Add a pool")
def add_pool(subnet_id: SubnetId, body: PoolIn, manager: Manager) -> dict[str, Any]:
    with manager.edit() as config:
        subnet = get_subnet(config, subnet_id)
        _check_pool(subnet, body.pool)
        pools = subnet.setdefault("pools", [])
        pools.append({"pool": body.pool})
    return {"index": len(pools) - 1, "pool": body.pool}


@router.put("/subnets/{subnet_id}/pools/{index}", tags=["Subnets & pools"], summary="Replace a pool")
def replace_pool(subnet_id: SubnetId, index: PoolIndex, body: PoolIn, manager: Manager) -> dict[str, Any]:
    with manager.edit() as config:
        subnet = get_subnet(config, subnet_id)
        pools = subnet.get("pools", [])
        if index >= len(pools):
            raise HTTPException(404, f"Subnet {subnet_id} has no pool at index {index}")
        _check_pool(subnet, body.pool, skip_index=index)
        pools[index] = {**pools[index], "pool": body.pool}
    return {"index": index, "pool": body.pool}


@router.delete("/subnets/{subnet_id}/pools/{index}", tags=["Subnets & pools"], summary="Delete a pool")
def delete_pool(subnet_id: SubnetId, index: PoolIndex, manager: Manager) -> dict[str, Any]:
    with manager.edit() as config:
        pools = get_subnet(config, subnet_id).get("pools", [])
        if index >= len(pools):
            raise HTTPException(404, f"Subnet {subnet_id} has no pool at index {index}")
        removed = pools.pop(index)
    return {"removed": removed.get("pool")}


# --- Options -----------------------------------------------------------------


def _option_data(config: dict[str, Any], subnet_id: int | None) -> list[dict[str, Any]]:
    scope = config["Dhcp6"] if subnet_id is None else get_subnet(config, subnet_id)
    return scope.setdefault("option-data", [])


def _set_option(manager: ConfigManager, subnet_id: int | None, name: OptionName, body: OptionValues) -> dict[str, Any]:
    try:
        values = normalise_option_values(name, body.values)
    except ValueError as exc:
        raise _invalid(str(exc)) from None
    with manager.edit() as config:
        set_option(_option_data(config, subnet_id), name.value, ", ".join(values))
    return {"name": name.value, "values": values}


def _delete_option(manager: ConfigManager, subnet_id: int | None, name: str) -> dict[str, Any]:
    with manager.edit() as config:
        if not remove_option(_option_data(config, subnet_id), name):
            raise HTTPException(404, f"Option '{name}' is not set")
    return {"removed": name}


@router.get("/options", tags=["Options"], summary="Global DHCPv6 options")
def get_global_options(manager: Manager) -> dict[str, str]:
    return options_to_dict(manager.read()["Dhcp6"].get("option-data", []))


@router.put(
    "/options/{name}", tags=["Options"], summary="Set a global option (dns-servers, domain-search, sntp-servers)"
)
def set_global_option(name: OptionName, body: OptionValues, manager: Manager) -> dict[str, Any]:
    return _set_option(manager, None, name, body)


@router.delete("/options/{name}", tags=["Options"], summary="Remove a global option")
def delete_global_option(name: str, manager: Manager) -> dict[str, Any]:
    return _delete_option(manager, None, name)


@router.get("/subnets/{subnet_id}/options", tags=["Options"], summary="Options of a subnet (override global ones)")
def get_subnet_options(subnet_id: SubnetId, manager: Manager) -> dict[str, str]:
    return options_to_dict(get_subnet(manager.read(), subnet_id).get("option-data", []))


@router.put("/subnets/{subnet_id}/options/{name}", tags=["Options"], summary="Set a subnet option")
def set_subnet_option(subnet_id: SubnetId, name: OptionName, body: OptionValues, manager: Manager) -> dict[str, Any]:
    return _set_option(manager, subnet_id, name, body)


@router.delete("/subnets/{subnet_id}/options/{name}", tags=["Options"], summary="Remove a subnet option")
def delete_subnet_option(subnet_id: SubnetId, name: str, manager: Manager) -> dict[str, Any]:
    return _delete_option(manager, subnet_id, name)


# --- Timers ------------------------------------------------------------------


def _current_timers(dhcp6: dict[str, Any]) -> Timers:
    return Timers(**{field: dhcp6.get(key) for field, key in TIMER_KEYS.items()})


@router.get("/timers", tags=["Timers"], response_model=Timers, summary="Global lease timers")
def get_timers(manager: Manager) -> Timers:
    return _current_timers(manager.read()["Dhcp6"])


@router.patch("/timers", tags=["Timers"], response_model=Timers, summary="Update some or all lease timers")
def update_timers(body: Timers, manager: Manager) -> Timers:
    with manager.edit() as config:
        dhcp6 = config["Dhcp6"]
        merged = _current_timers(dhcp6).model_copy(update=body.model_dump(exclude_none=True))
        try:
            check_timer_order(merged)
        except ValueError as exc:
            raise _invalid(str(exc)) from None
        for field, key in TIMER_KEYS.items():
            value = getattr(merged, field)
            if value is not None:
                dhcp6[key] = value
    return merged


# --- Leases ------------------------------------------------------------------


@router.get("/leases", tags=["Leases"], response_model=LeaseList, summary="Active leases")
def list_leases(
    request: Request,
    subnet_id: Annotated[int | None, Query(ge=1, description="Only leases from this subnet")] = None,
) -> LeaseList:
    leases = read_active_leases(request.app.state.settings.kea_leases_file)
    if subnet_id is not None:
        leases = [lease for lease in leases if lease.subnet_id == subnet_id]
    return LeaseList(count=len(leases), leases=leases)


# --- App factory -------------------------------------------------------------


def create_app(settings: Settings | None = None, kea: KeaControl | None = None) -> FastAPI:
    settings = settings or Settings()  # type: ignore[call-arg]
    kea = kea or KeaControl(settings.kea_control_socket, settings.kea_socket_timeout)

    app = FastAPI(title="DHCPv6 REST API", version=__version__, description=DESCRIPTION)
    app.state.settings = settings
    app.state.config = ConfigManager(ConfigFile(settings.kea_config_path), kea)

    @app.exception_handler(SubnetNotFound)
    async def _subnet_not_found(request: Request, exc: SubnetNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(KeaError)
    async def _kea_error(request: Request, exc: KeaError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(router)
    return app
