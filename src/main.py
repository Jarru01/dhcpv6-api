# Subor: /opt/dhcpv6-api/main.py
# Ucel: hlavna FastAPI aplikacia - definuje vsetky REST API endpointy
# Spustenie (manualne na testovanie):
#   cd /opt/dhcpv6-api && source venv/bin/activate
#   uvicorn main:app --host 0.0.0.0 --port 8000 --reload
# Spustenie (ako systemd sluzba):
#   sudo systemctl start dhcpv6-api
# Swagger UI dostupny na: http://158.193.154.109:8000/docs

import subprocess
from fastapi import FastAPI, HTTPException, Path as FPath
from typing import List
import kea                              # nas helper modul pre pracu s Kea
from models import Pool, TimersConfig   # Pydantic modely pre validaciu vstupov

# inicializacia FastAPI aplikacie - metadata sa zobrazuju v Swagger UI
app = FastAPI(
    title="DHCPv6 REST API",
    description="REST API pre spravu Kea DHCPv6 servera",
    version="1.0.0"
)

SUBNET_ID = 1   # ID subnetu s ktorym pracuju vsetky endpointy (zodpoveda 'id' v kea-dhcp6.conf)


# =============================================================================
# KONFGURACIA - citanie celej konfiguracie
# =============================================================================

@app.get("/api/v1/config", summary="Zobrazi celu aktualnu konfiguraciu")
def get_config():
    """
    Vrati celu Kea DHCPv6 konfiguraciu (len na citanie).
    Zodpoveda obsahu suboru /etc/kea/kea-dhcp6.conf - sekcia Dhcp6.
    Pouzitelne na overenie aktualneho stavu po zmene.
    """
    config = kea.load_config()
    return config["Dhcp6"]              # vracia len sekciu Dhcp6 (nie obalovy wrapper)


# =============================================================================
# STATUS - stav servera a sluzby
# =============================================================================

@app.get("/api/v1/status", summary="Stav servera")
def status():
    """
    Vracia stav oboch sluzby (kea-dhcp6-server a radvd), aktualny subnet,
    zoznam poolov a pocet aktivnych leasov.
    Hodnoty 'active' / 'inactive' zodpovedaju vystupu 'systemctl is-active'.
    """
    # overenie ci systemd sluzby bezi - pouziva 'systemctl is-active'
    kea_running = subprocess.run(
        ["systemctl", "is-active", "kea-dhcp6-server"],
        capture_output=True, text=True
    ).stdout.strip()

    radvd_running = subprocess.run(
        ["systemctl", "is-active", "radvd"],
        capture_output=True, text=True
    ).stdout.strip()

    config = kea.load_config()
    subnet = kea.get_subnet6(config, SUBNET_ID)
    leases = kea.get_leases()

    return {
        "kea_dhcp6":     kea_running,               # 'active' alebo 'inactive'
        "radvd":         radvd_running,              # 'active' alebo 'inactive'
        "subnet":        subnet["subnet"],            # napr. 'fd00::/64'
        "pools":         subnet.get("pools", []),    # zoznam aktivnych poolov
        "active_leases": len(leases),                # pocet aktivnych leasov (nie expiovanych)
        "options":       kea.option_data_to_dict(subnet.get("option-data", []))  # DNS atd.
    }


# =============================================================================
# POOLS - sprava adresnych poolov
# =============================================================================

@app.get("/api/v1/pools", summary="Zoznam vsetkych adresnych poolov")
def list_pools():
    """
    Vracia subnet a zoznam vsetkych adresnych poolov z konfiguracie.
    Index poolu (0-based) sa pouziva pri PUT a DELETE endpointoch.
    """
    config = kea.load_config()
    subnet = kea.get_subnet6(config, SUBNET_ID)
    return {
        "subnet": subnet["subnet"],         # napr. 'fd00::/64'
        "pools":  subnet.get("pools", [])   # napr. [{"pool": "fd00::100 - fd00::1ff"}]
    }


@app.post("/api/v1/pools", status_code=201, summary="Prida novy adresny pool")
def add_pool(pool: Pool):
    """
    Prida novy pool do konfiguracie a reloadne Kea server.
    Ak pool s rovnakym rozsahom uz existuje, vrati HTTP 409 Conflict.
    Format vstupu: {"pool": "fd00::200 - fd00::2ff"}
    """
    config = kea.load_config()
    subnet = kea.get_subnet6(config, SUBNET_ID)
    pools = subnet.setdefault("pools", [])  # ak 'pools' este neexistuje, vytvori prazdny zoznam

    # kontrola duplikat - ten isty pool nemoze byt pridany dvakrat
    for existing in pools:
        if existing["pool"] == pool.pool:
            raise HTTPException(status_code=409, detail="Pool uz existuje")

    pools.append({"pool": pool.pool})      # pridanie noveho poolu
    kea.save_config(config)                # zapis do suboru
    kea.reload_kea()                       # reload Kea - nacita novy konfig
    return {"message": "Pool pridany", "pool": pool.pool}


@app.put("/api/v1/pools/{pool_index}", summary="Nahradi pool na zadanom indexe")
def update_pool(
    pool: Pool,
    pool_index: int = FPath(..., ge=0, description="Index poolu (0-based)")
):
    """
    Nahradi existujuci pool na zadanom indexe novym poolom a reloadne Kea.
    Index je 0-based: prvy pool ma index 0, druhy 1 atd.
    Ak index neexistuje, vrati HTTP 404.
    Format vstupu: {"pool": "fd00::100 - fd00::1ff"}
    """
    config = kea.load_config()
    subnet = kea.get_subnet6(config, SUBNET_ID)
    pools = subnet.get("pools", [])

    if pool_index >= len(pools):
        raise HTTPException(status_code=404, detail="Pool s tymto indexom neexistuje")

    pools[pool_index] = {"pool": pool.pool}    # nahradenie poolu na zadanom indexe
    kea.save_config(config)
    kea.reload_kea()
    return {"message": "Pool aktualizovany", "pool": pool.pool}


@app.delete("/api/v1/pools/{pool_index}", summary="Zmaze pool podla indexu")
def delete_pool(
    pool_index: int = FPath(..., ge=0, description="Index poolu (0-based)")
):
    """
    Zmaze pool na zadanom indexe a reloadne Kea.
    Pozor: po zmazani sa indexy zvysnych poolov posunia.
    Ak index neexistuje, vrati HTTP 404.
    """
    config = kea.load_config()
    subnet = kea.get_subnet6(config, SUBNET_ID)
    pools = subnet.get("pools", [])

    if pool_index >= len(pools):
        raise HTTPException(status_code=404, detail="Pool s tymto indexom neexistuje")

    removed = pools.pop(pool_index)     # odstranenie poolu a ulozenie zmazaneho pre odpoved
    kea.save_config(config)
    kea.reload_kea()
    return {"message": "Pool zmazany", "removed": removed}


# =============================================================================
# OPTIONS - sprava DHCPv6 options (DNS, domain-search, NTP atd.)
# =============================================================================

@app.get("/api/v1/options", summary="Zobrazi aktualne DHCPv6 options")
def get_options():
    """
    Vracia globalne options (platne pre vsetky subnety), options specificke pre subnet 1
    a aktualne hodnoty vsetkych lease timerov.
    """
    config = kea.load_config()
    subnet = kea.get_subnet6(config, SUBNET_ID)
    return {
        "global_options": kea.option_data_to_dict(
            config["Dhcp6"].get("option-data", [])  # globalne options
        ),
        "subnet_options": kea.option_data_to_dict(
            subnet.get("option-data", [])            # options specificke pre subnet 1
        ),
        "timers": {
            "renew_timer":        config["Dhcp6"].get("renew-timer"),
            "rebind_timer":       config["Dhcp6"].get("rebind-timer"),
            "preferred_lifetime": config["Dhcp6"].get("preferred-lifetime"),
            "valid_lifetime":     config["Dhcp6"].get("valid-lifetime"),
        }
    }


@app.put("/api/v1/options/dns", summary="Nastavi DNS servery (option 23)")
def set_dns(dns_servers: List[str]):
    """
    Nastavi zoznam DNS serverov pre klientov - DHCPv6 option 23.
    Zmena sa zapise do globalnych options aj do options subnetu 1.
    Format vstupu: ["2001:4860:4860::8888", "2001:4860:4860::8844"]
    Prazdny zoznam nie je povoleny - vrati HTTP 400.
    """
    if not dns_servers:
        raise HTTPException(status_code=400, detail="Zoznam DNS serverov nesmi byt prazdny")
    config = kea.load_config()
    subnet = kea.get_subnet6(config, SUBNET_ID)
    value = ", ".join(dns_servers)              # Kea ocakava adresy ako 'addr1, addr2'
    kea.set_option(config["Dhcp6"].setdefault("option-data", []), "dns-servers", value)
    kea.set_option(subnet.setdefault("option-data", []),          "dns-servers", value)
    kea.save_config(config)
    kea.reload_kea()
    return {"message": "DNS servery aktualizovane", "dns-servers": dns_servers}


@app.put("/api/v1/options/domain-search", summary="Nastavi domain search list (option 24)")
def set_domain_search(domains: List[str]):
    """
    Nastavi zoznam domen pre hladanie (domain search list) - DHCPv6 option 24.
    Klienti pouzivaju tieto domeny pri rozlisovani krátkych hostnámov.
    Format vstupu: ["example.local", "corp.example.com"]
    """
    if not domains:
        raise HTTPException(status_code=400, detail="Zoznam domen nesmi byt prazdny")
    config = kea.load_config()
    subnet = kea.get_subnet6(config, SUBNET_ID)
    value = ", ".join(domains)
    kea.set_option(config["Dhcp6"].setdefault("option-data", []), "domain-search", value)
    kea.set_option(subnet.setdefault("option-data", []),          "domain-search", value)
    kea.save_config(config)
    kea.reload_kea()
    return {"message": "Domain search aktualizovany", "domain-search": domains}


@app.put("/api/v1/options/ntp", summary="Nastavi SNTP servery (option 31)")
def set_ntp(ntp_servers: List[str]):
    """
    Nastavi SNTP/NTP servery pre klientov - DHCPv6 option 31 (sntp-servers).

    POZOR: Kea 2.2.0 nepodporuje option 'ntp-server' (option 56 - RFC 5908).
    Pouzitie 'ntp-server' by sposobilo chybu pri nacitani konfigurácie a pad Kea servera.
    Tato implementacia preto vzdy pouziva 'sntp-servers' (option 31).

    Format vstupu: ["2001:4860:4860::8888"]
    """
    if not ntp_servers:
        raise HTTPException(status_code=400, detail="Zoznam NTP serverov nesmi byt prazdny")
    config = kea.load_config()
    subnet = kea.get_subnet6(config, SUBNET_ID)
    opt_name = "sntp-servers"           # VZDY sntp-servers (option 31) - nie ntp-server!
    value = ", ".join(ntp_servers)
    kea.set_option(config["Dhcp6"].setdefault("option-data", []), opt_name, value)
    kea.set_option(subnet.setdefault("option-data", []),          opt_name, value)
    kea.save_config(config)
    kea.reload_kea()
    return {"message": "SNTP servery aktualizovane", opt_name: ntp_servers}


@app.delete("/api/v1/options/{option_name}", summary="Zmaze konkretnu option")
def delete_option(option_name: str):
    """
    Zmaze option podla nazvu z globalnych options aj z options subnetu 1.
    Priklad: DELETE /api/v1/options/domain-search
    Ak option neexistuje ani v globalnych ani v subnetovych options, vrati HTTP 404.
    """
    config = kea.load_config()
    subnet = kea.get_subnet6(config, SUBNET_ID)

    def _remove(option_list: list, name: str) -> bool:
        """Pomocna funkcia: odstrani option zo zoznamu, vracia True ak nasla a zmazala."""
        original_len = len(option_list)
        option_list[:] = [o for o in option_list if o["name"] != name]
        return len(option_list) < original_len

    removed_global = _remove(config["Dhcp6"].get("option-data", []), option_name)
    removed_subnet = _remove(subnet.get("option-data", []),          option_name)

    if not removed_global and not removed_subnet:
        raise HTTPException(status_code=404, detail=f"Option '{option_name}' nenajdena")

    kea.save_config(config)
    kea.reload_kea()
    return {"message": f"Option '{option_name}' zmazana"}


# =============================================================================
# TIMERY - sprava DHCPv6 lease timerov
# =============================================================================

@app.put("/api/v1/timers", summary="Nastavi DHCPv6 lease timery")
def set_timers(timers: TimersConfig):
    """
    Nastavi jeden alebo viacero DHCPv6 lease timerov naraz.
    Vsetky pola su volitelne - nezadane pola zostanu nezmenene.
    Timery su globalne - platia pre vsetky subnety.
    Format vstupu: {"renew_timer": 1000, "rebind_timer": 2000, ...}
    Odporucane hodnoty: renew < rebind < preferred_lifetime < valid_lifetime
    """
    config = kea.load_config()
    dhcp6 = config["Dhcp6"]
    # aktualizuj len tie timery ktore boli zadane v requeste (nie None)
    if timers.renew_timer        is not None: dhcp6["renew-timer"]        = timers.renew_timer
    if timers.rebind_timer       is not None: dhcp6["rebind-timer"]       = timers.rebind_timer
    if timers.preferred_lifetime is not None: dhcp6["preferred-lifetime"] = timers.preferred_lifetime
    if timers.valid_lifetime     is not None: dhcp6["valid-lifetime"]     = timers.valid_lifetime
    kea.save_config(config)
    kea.reload_kea()
    return {
        "message": "Timery aktualizovane",
        "timers": {
            "renew-timer":        dhcp6.get("renew-timer"),
            "rebind-timer":       dhcp6.get("rebind-timer"),
            "preferred-lifetime": dhcp6.get("preferred-lifetime"),
            "valid-lifetime":     dhcp6.get("valid-lifetime"),
        }
    }


# =============================================================================
# LEASY - zobrazenie aktivnych DHCPv6 leasov
# =============================================================================

@app.get("/api/v1/leases", summary="Zoznam aktivnych DHCPv6 lease zaznamov")
def list_leases():
    """
    Vracia vsetky aktivne lease zaznamy z Kea memfile CSV suboru.
    Expiovane zaznamy (valid_lifetime=0) su automaticky preskocene.
    Kazdy zaznam obsahuje: IPv6 adresu, DUID klienta, cas exspiracy a dalsi metadata.
    """
    leases = kea.get_leases()
    return {"count": len(leases), "leases": leases}


# =============================================================================
# RELOAD - manualny reload Kea konfiguracie
# =============================================================================

@app.post("/api/v1/reload", summary="Reloaduje Kea DHCPv6 server")
def reload_server():
    """
    Vynuti Kea server nacitat aktualny konfiguracny subor.
    Pouzitelne po manualnej zmene /etc/kea/kea-dhcp6.conf cez terminal.
    Automaticky reloaduje aj po vsetkych POST/PUT/DELETE operaciach.
    """
    result = kea.reload_kea()
    return {"message": "Reload dokonceny", "kea_response": result}
