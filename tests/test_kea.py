from __future__ import annotations

import json
import os
import socket
import sys
import threading

import pytest

from dhcpv6_api.kea import ConfigFile, ConfigFileError, KeaControl, KeaUnavailable, parse_config, strip_comments
from dhcpv6_api.leases import read_active_leases


def test_strip_comments_keeps_strings_intact():
    text = """
    # shell comment
    {
      // line comment
      "url": "http://example.com/#anchor", /* block
      comment */ "n": 1
    }
    """
    assert json.loads(strip_comments(text)) == {"url": "http://example.com/#anchor", "n": 1}


def test_parse_config_errors():
    with pytest.raises(ConfigFileError):
        parse_config("{ not json")
    with pytest.raises(ConfigFileError):
        parse_config('{"Dhcp4": {}}')


def test_config_file_atomic_write_keeps_mode(tmp_path):
    path = tmp_path / "kea.conf"
    path.write_text("{}")
    os.chmod(path, 0o640)
    ConfigFile(path).write({"Dhcp6": {}})
    assert json.loads(path.read_text()) == {"Dhcp6": {}}
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o640
    assert [p.name for p in tmp_path.iterdir()] == ["kea.conf"]


def test_missing_config_file(tmp_path):
    with pytest.raises(ConfigFileError):
        ConfigFile(tmp_path / "nope.conf").read()


HEADER = (
    "address,duid,valid_lifetime,expire,subnet_id,pref_lifetime,lease_type,iaid,prefix_len,"
    "fqdn_fwd,fqdn_rev,hostname,hwaddr,state,user_context\n"
)


def test_leases_last_row_wins_and_expired_are_dropped(tmp_path):
    path = tmp_path / "leases.csv"
    now = 1_000_000
    path.write_text(
        HEADER
        # one client renewing three times: only the newest row counts
        + f"fd00::100,00:01,4000,{now - 10},1,3000,0,1,128,0,0,,,0,\n"
        + f"fd00::100,00:01,4000,{now + 100},1,3000,0,1,128,0,0,,,0,\n"
        + f"fd00::100,00:01,4000,{now + 4000},1,3000,0,1,128,0,0,pc&#x2cone,,0,\n"
        # expired
        + f"fd00::101,00:02,4000,{now - 1},1,3000,0,1,128,0,0,,,0,\n"
        # released (valid_lifetime 0)
        + f"fd00::102,00:03,0,{now + 50},1,0,0,1,128,0,0,,,0,\n"
        # declined (state 1)
        + f"fd00::103,00:04,4000,{now + 50},1,3000,0,1,128,0,0,,,1,\n"
        # renewed then released: last row says released
        + f"fd00::104,00:05,4000,{now + 50},1,3000,0,1,128,0,0,,,0,\n"
        + f"fd00::104,00:05,0,{now},1,0,0,1,128,0,0,,,0,\n"
    )
    leases = read_active_leases(path, now=now)
    assert [lease.address for lease in leases] == ["fd00::100"]
    assert leases[0].hostname == "pc,one"
    assert leases[0].lease_type == "IA_NA"


def test_missing_lease_file_means_no_leases(tmp_path):
    assert read_active_leases(tmp_path / "missing.csv") == []


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX") or sys.platform == "win32", reason="needs UNIX sockets")
def test_control_socket_round_trip(tmp_path):
    path = str(tmp_path / "kea.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(1)
    received = {}

    def serve():
        conn, _ = server.accept()
        with conn:
            received.update(json.loads(conn.recv(65536)))
            # send the reply in two pieces to exercise the read loop
            reply = json.dumps({"result": 0, "text": "ok", "arguments": {"uptime": 7}}).encode()
            conn.sendall(reply[:10])
            conn.sendall(reply[10:])

    thread = threading.Thread(target=serve)
    thread.start()
    status = KeaControl(path, timeout=2).status()
    thread.join()
    server.close()
    assert received == {"command": "status-get"}
    assert status == {"uptime": 7}


def test_unreachable_socket(tmp_path):
    with pytest.raises(KeaUnavailable):
        KeaControl(tmp_path / "missing.sock", timeout=0.5).command("status-get")
