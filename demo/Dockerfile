# Self-contained demo: Kea DHCPv6 (Debian 12 package, Kea 2.2) + the REST API
# in one container, talking over the real control socket.
FROM debian:12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends kea-dhcp6-server python3 python3-venv \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/dhcpv6-api
COPY pyproject.toml README.md ./
COPY src ./src
RUN python3 -m venv venv && venv/bin/pip install --no-cache-dir .

COPY demo/kea-dhcp6.conf /etc/kea/kea-dhcp6.conf
COPY demo/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
ENTRYPOINT ["/entrypoint.sh"]
