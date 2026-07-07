# Subor: /opt/dhcpv6-api/kea.py
# Ucel: pomocne funkcie pre pracu s Kea DHCPv6 serverom
# Obsahuje: citanie/zapis konfiguracie, reload servera, citanie leasov,
#           pracu so subnetmi a option-data zoznamami
# Tento modul importuje main.py - nema vlastny HTTP server

import json
import subprocess
import socket
import os
from pathlib import Path

# cesty k suborom a socketom Kea servera
KEA_CONFIG_PATH = "/etc/kea/kea-dhcp6.conf"         # hlavny konfiguracny subor Kea
KEA_CTRL_SOCKET = "/run/kea/kea6-ctrl-socket"        # UNIX socket pre live komunikaciu s Kea
LEASES_FILE    = "/var/lib/kea/kea-leases6.csv"      # CSV subor s aktualnymi leasmi
SOCKET_TIMEOUT = 10                                   # timeout pre socket komunikaciu v sekundach


def load_config() -> dict:
    """
    Nacita aktualnu Kea konfiguraciu zo suboru kea-dhcp6.conf.
    Vracia Python slovnik (dict) parsovany z JSON.
    Vyhodi IOError ak subor neexistuje, alebo JSONDecodeError ak je poskodeny.
    """
    with open(KEA_CONFIG_PATH, "r") as f:
        return json.load(f)


def save_config(config: dict) -> None:
    """
    Ulozi konfiguraciu do suboru kea-dhcp6.conf.
    Pouziva atomicky zapis cez docasny .tmp subor a os.replace():
    - najprv zapise do .tmp suboru
    - potom atomicky premenuje .tmp → finalny subor
    Tym sa zabrani poskodeniu konfig suboru ak by zapis bol prerušeny (napr. vypadok prúdu)
    """
    tmp_path = KEA_CONFIG_PATH + ".tmp"     # docasny subor pre atomicky zapis
    with open(tmp_path, "w") as f:
        json.dump(config, f, indent=2)      # zapise formatovany JSON (indent=2 = 2 medzery)
    os.replace(tmp_path, KEA_CONFIG_PATH)   # atomicka nahrada - bud prebehne cely alebo vobec


def reload_kea() -> dict:
    """
    Pokusi sa reloadnut Kea server - najprv cez control socket (rychlejsie, bez vypadku),
    ak socket nie je dostupny, zalohovo spusti systemctl reload-or-restart.

    Primarny sposob (control socket):
    - posle JSON prikaz config-reload priamo beziacemu Kea procesu
    - Kea nacita novy konfig bez prerusenia beziacich leasov

    Zalohy sposob (systemctl):
    - vyzaduje sudo NOPASSWD pre avs v /etc/sudoers.d/kea-api
    - spusti: sudo /usr/bin/systemctl reload-or-restart kea-dhcp6-server
    """
    if os.path.exists(KEA_CTRL_SOCKET):     # skontroluje ci Kea bezi a socket existuje
        try:
            return _socket_command({"command": "config-reload", "arguments": {}})
        except Exception:
            pass                            # ak socket zlyha, pokracuje na systemctl

    # zalohy sposob cez systemctl
    result = subprocess.run(
        ["sudo", "/usr/bin/systemctl", "reload-or-restart", "kea-dhcp6-server"],
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        # systemctl zapisuje chyby do journalu, nie stderr - zachytime oba vystupy
        detail = result.stderr.strip() or result.stdout.strip() or "pozri: journalctl -u kea-dhcp6-server"
        raise RuntimeError(f"Reload zlyhal (exit {result.returncode}): {detail}")
    return {"result": 0, "text": "Reloaded via systemctl"}


def _socket_command(command: dict) -> dict:
    """
    Interna funkcia: odosle JSON prikaz na Kea UNIX control socket a vraci odpoved.
    Kea vracia odpoved vo formate JSON pole napr. [{"result": 0, "text": "..."}]
    Funkcia vracia prvy prvok pola (najcastejsi pripad).
    Pouziva sa len z reload_kea() - nie je volana priamo z main.py
    """
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(SOCKET_TIMEOUT)
        sock.connect(KEA_CTRL_SOCKET)
        sock.sendall(json.dumps(command).encode())  # posle JSON prikaz ako bajty
        response = b""
        while True:
            try:
                chunk = sock.recv(4096)             # cita odpoved po 4096 bajtoch
                if not chunk:
                    break                           # koniec spojenia
                response += chunk
            except socket.timeout:
                break                               # timeout - vsetko uz prislo
    parsed = json.loads(response)
    if isinstance(parsed, list):                    # Kea vracia pole - vratime prvy prvok
        return parsed[0]
    return parsed


def get_leases() -> list:
    """
    Nacita aktivne lease zaznamy z Kea memfile CSV suboru.
    Preskakuje zaznamy kde valid_lifetime=0 (expiovane alebo manualnie zmazane leasy).
    Vracia zoznam slovnikov kde kazdy slovnik = jeden lease zaznam.
    Ak subor neexistuje (este neboli pridelene ziadne adresy), vracia prazdny zoznam.
    """
    leases = []
    if not Path(LEASES_FILE).exists():
        return leases                               # subor este neexistuje - ziadne leasy
    with open(LEASES_FILE, "r") as f:
        lines = f.readlines()
    if len(lines) < 2:
        return leases                               # subor ma len hlavicku alebo je prazdny
    headers = [h.strip() for h in lines[0].split(",")]  # prvy riadok = hlavicka CSV
    for line in lines[1:]:
        line = line.strip()
        if not line or line.startswith("#"):
            continue                                # preskoci prazdne riadky a komentare
        values = line.split(",")
        if len(values) == len(headers):
            lease = dict(zip(headers, values))      # spoji hlavicku s hodnotami do slovnika
            if lease.get("valid_lifetime", "1") != "0":  # preskoci expiovane leasy
                leases.append(lease)
    return leases


def get_subnet6(config: dict, subnet_id: int = 1) -> dict:
    """
    Vrati konfiguraciu subnetu podla jeho ID z nacitanej konfiguracie.
    Parametr subnet_id zodpoveda polu 'id' v kea-dhcp6.conf (default = 1).
    Vyhodi ValueError ak subnet s danym ID neexistuje - FastAPI to zachyti a vrati 404.
    """
    subnets = config["Dhcp6"].get("subnet6", [])
    for subnet in subnets:
        if subnet.get("id") == subnet_id:
            return subnet
    raise ValueError(f"Subnet s ID {subnet_id} neexistuje")


def option_data_to_dict(option_data: list) -> dict:
    """
    Prevedie zoznam option-data objektov (format Kea JSON) na jednoduchy slovnik name → data.
    Vstup:  [{"name": "dns-servers", "data": "2001:..."}, ...]
    Vystup: {"dns-servers": "2001:...", ...}
    Pouziva sa v GET endpointoch pre lahsie citanie odpovede.
    """
    return {opt["name"]: opt.get("data", "") for opt in option_data}


def set_option(option_list: list, name: str, value: str) -> None:
    """
    Nastavi alebo prida option v zozname option-data.
    Ak option s danym nazvom uz existuje: aktualizuje hodnotu 'data'.
    Ak option este neexistuje: prida novy zaznam na koniec zoznamu.
    Funkcia modifikuje zoznam priamo (in-place) - caller musi potom volat save_config().
    """
    for opt in option_list:
        if opt["name"] == name:
            opt["data"] = value     # aktualizacia existujucej option
            return
    option_list.append({"name": name, "data": value})  # pridanie novej option
