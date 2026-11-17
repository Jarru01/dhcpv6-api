from __future__ import annotations

import json

import pytest

from dhcpv6_api.kea import strip_comments


def read(path):
    return json.loads(strip_comments(path.read_text()))


# --- auth --------------------------------------------------------------------


def test_requests_without_key_are_rejected(client):
    r = client.get("/api/v1/config", headers={"X-API-Key": ""})
    assert r.status_code == 401
    r = client.get("/api/v1/config", headers={"X-API-Key": "wrong-key-wrong-key"})
    assert r.status_code == 401


def test_healthz_is_public(client):
    assert client.get("/healthz", headers={"X-API-Key": ""}).json() == {"status": "ok"}


def test_short_api_key_is_refused():
    from pydantic import ValidationError

    from dhcpv6_api.settings import Settings

    with pytest.raises(ValidationError):
        Settings(api_key="short")


# --- reading -----------------------------------------------------------------


def test_reads_commented_config(client):
    r = client.get("/api/v1/config")
    assert r.status_code == 200
    assert r.json()["subnet6"][0]["subnet"] == "fd00::/64"


def test_status(client):
    body = client.get("/api/v1/status").json()
    assert body["kea_dhcp6"] == "active"
    assert body["kea_uptime_s"] == 120
    assert body["subnets"] == 1


def test_unknown_subnet_is_404(client):
    assert client.get("/api/v1/subnets/42/pools").status_code == 404


def test_list_subnets(client):
    [subnet] = client.get("/api/v1/subnets").json()
    assert subnet["pools"] == ["fd00::100 - fd00::1ff"]
    assert subnet["options"]["domain-search"] == "example.local"


# --- pools -------------------------------------------------------------------


def test_add_pool_is_tested_written_and_reloaded(client, fake_kea, config_path):
    r = client.post("/api/v1/subnets/1/pools", json={"pool": "fd00::0200 - fd00::2ff"})
    assert r.status_code == 201
    assert r.json() == {"index": 1, "pool": "fd00::200 - fd00::2ff"}
    assert len(fake_kea.tested) == 1 and fake_kea.reloads == 1
    assert read(config_path)["Dhcp6"]["subnet6"][0]["pools"][1] == {"pool": "fd00::200 - fd00::2ff"}


def test_prefix_pool_is_accepted(client):
    r = client.post("/api/v1/subnets/1/pools", json={"pool": "fd00::1:0/112"})
    assert r.status_code == 201


@pytest.mark.parametrize(
    "pool",
    ["not-an-ip; rm -rf /", "fd00::2ff - fd00::200", "fd00::1 - fd00::2 - fd00::3", "fd00::1/120"],
)
def test_malformed_pools_are_rejected(client, fake_kea, pool):
    assert client.post("/api/v1/subnets/1/pools", json={"pool": pool}).status_code == 422
    assert fake_kea.reloads == 0


def test_pool_outside_subnet_is_rejected(client, fake_kea):
    r = client.post("/api/v1/subnets/1/pools", json={"pool": "2001:db8::1 - 2001:db8::5"})
    assert r.status_code == 422
    assert fake_kea.reloads == 0


@pytest.mark.parametrize("pool", ["fd00::100 - fd00::1ff", "fd00::150 - fd00::250", "fd00::100/120"])
def test_overlapping_pool_is_conflict(client, pool):
    assert client.post("/api/v1/subnets/1/pools", json={"pool": pool}).status_code == 409


def test_replace_pool_may_overlap_itself(client):
    r = client.put("/api/v1/subnets/1/pools/0", json={"pool": "fd00::100 - fd00::3ff"})
    assert r.status_code == 200


def test_replace_and_delete_missing_index(client):
    assert client.put("/api/v1/subnets/1/pools/5", json={"pool": "fd00::900 - fd00::9ff"}).status_code == 404
    assert client.delete("/api/v1/subnets/1/pools/5").status_code == 404


def test_delete_pool(client, config_path):
    assert client.delete("/api/v1/subnets/1/pools/0").json() == {"removed": "fd00::100 - fd00::1ff"}
    assert read(config_path)["Dhcp6"]["subnet6"][0]["pools"] == []


# --- safe apply --------------------------------------------------------------


def test_config_rejected_by_kea_is_not_written(client, fake_kea, config_path):
    before = config_path.read_text()
    fake_kea.reject_with = "pool out of range"
    r = client.post("/api/v1/subnets/1/pools", json={"pool": "fd00::200 - fd00::2ff"})
    assert r.status_code == 422
    assert "pool out of range" in r.json()["detail"]
    assert config_path.read_text() == before
    assert fake_kea.reloads == 0


def test_failed_reload_restores_previous_config(client, fake_kea, config_path):
    before = config_path.read_text()
    fake_kea.fail_reloads = 1
    r = client.post("/api/v1/subnets/1/pools", json={"pool": "fd00::200 - fd00::2ff"})
    assert r.status_code == 502
    assert config_path.read_text() == before
    assert fake_kea.reloads == 2  # failed attempt + reload of the restored file


def test_kea_unreachable_is_503(make_client, config_path):
    client = make_client()  # real KeaControl pointing at a socket that does not exist
    before = config_path.read_text()
    r = client.post("/api/v1/subnets/1/pools", json={"pool": "fd00::200 - fd00::2ff"})
    assert r.status_code == 503
    assert config_path.read_text() == before
    assert client.get("/api/v1/status").json()["kea_dhcp6"] == "unreachable"


# --- options -----------------------------------------------------------------


def test_set_global_dns(client, config_path):
    r = client.put("/api/v1/options/dns-servers", json={"values": ["2001:4860:4860:0::8888"]})
    assert r.status_code == 200
    assert r.json()["values"] == ["2001:4860:4860::8888"]
    options = read(config_path)["Dhcp6"]["option-data"]
    assert {"name": "dns-servers", "data": "2001:4860:4860::8888"} in options


def test_set_subnet_sntp(client, config_path):
    r = client.put("/api/v1/subnets/1/options/sntp-servers", json={"values": ["fd00::1", "fd00::2"]})
    assert r.status_code == 200
    assert client.get("/api/v1/subnets/1/options").json()["sntp-servers"] == "fd00::1, fd00::2"


@pytest.mark.parametrize(
    ("name", "values"),
    [("dns-servers", ["hello"]), ("dns-servers", ["8.8.8.8"]), ("domain-search", ["bad domain"]), ("dns-servers", [])],
)
def test_invalid_option_values(client, fake_kea, name, values):
    assert client.put(f"/api/v1/options/{name}", json={"values": values}).status_code == 422
    assert fake_kea.reloads == 0


def test_unsupported_option_name(client):
    assert client.put("/api/v1/options/ntp-server", json={"values": ["fd00::1"]}).status_code == 422


def test_delete_option(client):
    assert client.delete("/api/v1/options/domain-search").status_code == 200
    assert "domain-search" not in client.get("/api/v1/options").json()
    assert client.delete("/api/v1/options/domain-search").status_code == 404


# --- timers ------------------------------------------------------------------


def test_get_and_patch_timers(client, config_path):
    assert client.get("/api/v1/timers").json()["valid_lifetime"] == 4000
    r = client.patch("/api/v1/timers", json={"preferred_lifetime": 3600, "valid_lifetime": 7200})
    assert r.status_code == 200
    assert r.json() == {"renew_timer": 1000, "rebind_timer": 2000, "preferred_lifetime": 3600, "valid_lifetime": 7200}
    assert read(config_path)["Dhcp6"]["valid-lifetime"] == 7200


@pytest.mark.parametrize(
    "body",
    [{"renew_timer": 9000}, {"valid_lifetime": 10}, {"rebind_timer": 500}, {"renew_timer": 0}],
)
def test_inconsistent_timers_are_rejected(client, fake_kea, body):
    assert client.patch("/api/v1/timers", json=body).status_code == 422
    assert fake_kea.reloads == 0


# --- leases ------------------------------------------------------------------


def test_leases_endpoint_filters_by_subnet(client, leases_path):
    leases_path.write_text(
        "address,duid,valid_lifetime,expire,subnet_id,pref_lifetime,lease_type,iaid,prefix_len,"
        "fqdn_fwd,fqdn_rev,hostname,hwaddr,state,user_context\n"
        "fd00::100,00:01,4000,4102444800,1,3000,0,1,128,0,0,client1,,0,\n"
        "fd01::100,00:02,4000,4102444800,2,3000,0,1,128,0,0,,,0,\n"
    )
    assert client.get("/api/v1/leases").json()["count"] == 2
    body = client.get("/api/v1/leases", params={"subnet_id": 1}).json()
    assert body["count"] == 1
    assert body["leases"][0]["hostname"] == "client1"


def test_reload(client, fake_kea):
    assert client.post("/api/v1/reload").status_code == 200
    assert fake_kea.reloads == 1
