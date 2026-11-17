from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from DHCPV6_* environment variables or .env."""

    model_config = SettingsConfigDict(env_prefix="DHCPV6_", env_file=".env", extra="ignore")

    api_key: SecretStr
    kea_config_path: Path = Path("/etc/kea/kea-dhcp6.conf")
    kea_control_socket: Path = Path("/run/kea/kea6-ctrl-socket")
    kea_leases_file: Path = Path("/var/lib/kea/kea-leases6.csv")
    kea_socket_timeout: float = 10.0

    @field_validator("api_key")
    @classmethod
    def _key_is_strong(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 16:
            raise ValueError("DHCPV6_API_KEY must be at least 16 characters")
        return value
