# DHCPv6 Server Configuration Interface

This repository contains a FastAPI-based web application and API designed to manage, configure, and monitor a **Kea DHCPv6 server** and the **Router Advertisement Daemon (radvd)**. It provides a clean web user interface (via FastAPI's interactive documentation/GUI) to dynamically manipulate IPv6 network configurations, parse service models, and manage system services.

---

## 🚀 Features

* **FastAPI Backend:** High-performance API endpoints to read, validate, and update DHCPv6 and radvd configurations.
* **Interactive GUI:** Built-in web interface (Swagger UI) for effortless configuration management without touching the CLI.
* **Kea DHCPv6 Integration:** Handles structured configuration updates for Kea's modern DHCPv6 engine.
* **SLAAC Support:** Manages `radvd` configurations alongside DHCPv6 to support stateful/stateless address autoconfiguration.
* **Production Ready:** Includes a `systemd` service unit file to deploy the API seamlessly as a background daemon on Linux.

---

## 📁 Repository Structure

```text
dhcpv6-api/
├── src/
│   ├── main.py                       # FastAPI entry point: app and routes
│   ├── kea.py                        # Kea config parsing, editing, reload
│   └── models.py                     # Pydantic request models
├── config/
│   ├── kea-dhcp6.conf                # Kea DHCPv6 server configuration
│   ├── radvd.conf                    # Router Advertisement Daemon configuration
│   ├── 99-ipv6.conf                  # sysctl IPv6 forwarding settings
│   └── netplan/
│       ├── 51-ipv6-SERVER.yaml       # static IPv6 address for the server (ens4)
│       └── 51-ipv6-CLIENT.yaml       # matching client-side netplan config
├── deploy/
│   ├── dhcpv6-api.service            # systemd unit for the API
│   └── avs-template.yaml             # HEAT template provisioning the lab machines
├── requirements.txt                  # Python dependencies
└── README.md
```

---

## 🛠️ Prerequisites

Ensure your system has the following packages installed:
* Python 3.8+
* Kea DHCPv6 Server (`isc-kea-dhcp6-server`)
* Router Advertisement Daemon (`radvd`)

Install the Python dependencies:
```bash
pip install -r requirements.txt
```

---

## 🚀 Deployment

The API runs on the DHCPv6 server itself and expects the layout below. The
application code uses absolute paths, so keep the target locations fixed:

| Repo path | Target on the server |
| :--- | :--- |
| `src/*` | `/opt/dhcpv6-api/` (systemd `WorkingDirectory`; run with `uvicorn main:app --host 0.0.0.0 --port 8000`) |
| `config/kea-dhcp6.conf` | `/etc/kea/kea-dhcp6.conf` |
| `config/radvd.conf` | `/etc/radvd.conf` |
| `config/99-ipv6.conf` | `/etc/sysctl.d/99-ipv6.conf` |
| `config/netplan/*.yaml` | `/etc/netplan/` |
| `deploy/dhcpv6-api.service` | `/etc/systemd/system/dhcpv6-api.service` |
