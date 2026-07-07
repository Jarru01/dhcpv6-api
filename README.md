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

Based on the project architecture, here is an overview of the core files:

| File / Folder | Description |
| :--- | :--- |
| **`main.py`** | The entry point of the application; initializes the FastAPI app and routes. |
| **`kea.py`** | Contains the core logic for interacting with, parsing, and modifying Kea configurations. |
| **`models.py`** | Defines Pydantic data models for structured request validation (subnets, pools, options). |
| **`kea-dhcp6.conf`** | The base or active configuration file for the Kea DHCPv6 server. |
| **`radvd.conf`** | The configuration file for the Router Advertisement Daemon. |
| **`99-ipv6.conf`** | System sysctl configuration file to optimize/enable IPv6 forwarding and behavior. |
| **`dhcpv6-api.service`** | Systemd unit file to manage the FastAPI application as a system service. |
| **`51-ipv6-SERVER`** / **`CLIENT`** | Network configuration or automation scripts for the server/client environments. |
| **`avs-template`** | Environment or virtualization template file used during setup. |

---

## 🛠️ Prerequisites

Ensure your system has the following packages installed:
* Python 3.8+
* Kea DHCPv6 Server (`isc-kea-dhcp6-server`)
* Router Advertisement Daemon (`radvd`)

Install the Python dependencies:
```bash
pip install fastapi uvicorn pydantic