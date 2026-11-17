"""Access to Kea: the config file on disk and the control socket of the running server."""

from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any


class KeaError(Exception):
    """Kea returned an error or could not be talked to."""

    status_code = 502


class KeaUnavailable(KeaError):
    status_code = 503


class KeaConfigRejected(KeaError):
    status_code = 422


class ConfigFileError(KeaError):
    status_code = 500


class SubnetNotFound(LookupError):
    def __init__(self, subnet_id: int) -> None:
        super().__init__(f"Subnet with id {subnet_id} does not exist")


def strip_comments(text: str) -> str:
    """Remove the comment styles Kea allows (//, /* */ and #) outside of JSON strings."""
    out: list[str] = []
    i, n = 0, len(text)
    in_string = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif ch == '"':
                in_string = False
            i += 1
        elif ch == '"':
            in_string = True
            out.append(ch)
            i += 1
        elif text.startswith("//", i) or ch == "#":
            while i < n and text[i] != "\n":
                i += 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def parse_config(text: str) -> dict[str, Any]:
    try:
        config = json.loads(strip_comments(text))
    except json.JSONDecodeError as exc:
        raise ConfigFileError(f"Kea config is not valid JSON: {exc}") from exc
    if not isinstance(config, dict) or not isinstance(config.get("Dhcp6"), dict):
        raise ConfigFileError("Kea config has no 'Dhcp6' object")
    return config


class ConfigFile:
    """The Kea config file. Writes are atomic and keep the file's mode and group."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def read_text(self) -> str:
        try:
            return self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigFileError(f"Cannot read {self.path}: {exc.strerror}") from exc

    def read(self) -> dict[str, Any]:
        return parse_config(self.read_text())

    def write(self, config: dict[str, Any]) -> None:
        self.write_text(json.dumps(config, indent=2) + "\n")

    def write_text(self, text: str) -> None:
        try:
            original = self.path.stat()
        except FileNotFoundError:
            original = None
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            if original is not None:
                os.chmod(tmp, original.st_mode & 0o7777)
                if hasattr(os, "chown"):
                    with suppress(PermissionError):
                        os.chown(tmp, -1, original.st_gid)
            os.replace(tmp, self.path)
        except OSError as exc:
            Path(tmp).unlink(missing_ok=True)
            raise ConfigFileError(f"Cannot write {self.path}: {exc.strerror}") from exc


class KeaControl:
    """Client for the kea-dhcp6 UNIX control socket (one command per connection)."""

    def __init__(self, socket_path: Path, timeout: float = 10.0) -> None:
        self.socket_path = Path(socket_path)
        self.timeout = timeout

    def command(self, command: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        request: dict[str, Any] = {"command": command}
        if arguments is not None:
            request["arguments"] = arguments
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(self.timeout)
                sock.connect(str(self.socket_path))
                sock.sendall(json.dumps(request).encode())
                response = self._read_response(sock)
        except (OSError, AttributeError) as exc:
            raise KeaUnavailable(f"Kea control socket {self.socket_path} is not reachable: {exc}") from exc
        # kea-dhcp6 answers with an object; the Control Agent wraps it in a list.
        if isinstance(response, list):
            response = response[0] if response else {}
        return response

    @staticmethod
    def _read_response(sock: socket.socket) -> Any:
        buf = b""
        while True:
            chunk = sock.recv(65536)
            if chunk:
                buf += chunk
            try:
                return json.loads(buf)
            except json.JSONDecodeError:
                if not chunk:
                    raise KeaError(f"Incomplete response from Kea: {buf[:200]!r}") from None

    def config_test(self, config: dict[str, Any]) -> None:
        response = self.command("config-test", config)
        if response.get("result") != 0:
            raise KeaConfigRejected(f"Kea rejected the configuration: {response.get('text')}")

    def config_reload(self) -> dict[str, Any]:
        response = self.command("config-reload")
        if response.get("result") != 0:
            raise KeaError(f"Kea failed to reload its configuration: {response.get('text')}")
        return response

    def status(self) -> dict[str, Any]:
        response = self.command("status-get")
        if response.get("result") != 0:
            raise KeaError(f"status-get failed: {response.get('text')}")
        return response.get("arguments", {})


class ConfigManager:
    """Serialises config changes and applies them safely.

    A change is validated by Kea (config-test) before anything is written. If the
    reload fails after writing, the previous file is restored and reloaded.
    """

    def __init__(self, file: ConfigFile, kea: KeaControl) -> None:
        self.file = file
        self.kea = kea
        self._lock = threading.Lock()

    def read(self) -> dict[str, Any]:
        return self.file.read()

    def reload(self) -> dict[str, Any]:
        with self._lock:
            return self.kea.config_reload()

    @contextmanager
    def edit(self) -> Iterator[dict[str, Any]]:
        with self._lock:
            previous = self.file.read_text()
            config = parse_config(previous)
            yield config
            self.kea.config_test(config)
            self.file.write(config)
            try:
                self.kea.config_reload()
            except KeaError:
                self.file.write_text(previous)
                with suppress(KeaError):
                    self.kea.config_reload()
                raise


def get_subnet(config: dict[str, Any], subnet_id: int) -> dict[str, Any]:
    for subnet in config["Dhcp6"].get("subnet6", []):
        if subnet.get("id") == subnet_id:
            return subnet
    raise SubnetNotFound(subnet_id)


def options_to_dict(option_data: list[dict[str, Any]]) -> dict[str, str]:
    return {opt.get("name", f"code-{opt.get('code')}"): opt.get("data", "") for opt in option_data}


def set_option(option_data: list[dict[str, Any]], name: str, value: str) -> None:
    for opt in option_data:
        if opt.get("name") == name:
            opt["data"] = value
            return
    option_data.append({"name": name, "data": value})


def remove_option(option_data: list[dict[str, Any]], name: str) -> bool:
    before = len(option_data)
    option_data[:] = [opt for opt in option_data if opt.get("name") != name]
    return len(option_data) < before
