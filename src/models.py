# Subor: /opt/dhcpv6-api/models.py
# Ucel: definicia Pydantic datovych modelov pre validaciu vstupov REST API
# Pydantic automaticky validuje vstupne data z HTTP requestov pred spracovanim
# Ak data nespĺňaju podmienky (napr. chybajuce pole), FastAPI vrati HTTP 422

from pydantic import BaseModel, Field
from typing import Optional


class Pool(BaseModel):
    """
    Model pre adresny pool DHCPv6.
    Pouziva sa pri POST /api/v1/pools a PUT /api/v1/pools/{index}
    """
    pool: str = Field(
        ...,                                    # tri bodky = pole je povinne (required)
        example="fd00::100 - fd00::1ff",        # priklad hodnoty zobrazeny v Swagger UI
        description="Rozsah adries vo formate 'start - end'"  # popis v Swagger UI
    )


class TimersConfig(BaseModel):
    """
    Model pre konfiguraciu DHCPv6 lease timerov.
    Pouziva sa pri PUT /api/v1/timers.
    Vsetky pola su volitelne - mozno zmenit len jeden timer bez ovplyvnenia ostatnych.
    """
    renew_timer: Optional[int] = Field(
        None,                       # None = volitelne pole, nebude zmene ak nie je zadane
        ge=1,                       # ge = greater or equal, hodnota musi byt minimalnie 1
        description="T1 timer v sekundach - kedy klient posle Renew"
    )
    rebind_timer: Optional[int] = Field(
        None,
        ge=1,
        description="T2 timer v sekundach - kedy klient posle Rebind ak Renew zlyha"
    )
    preferred_lifetime: Optional[int] = Field(
        None,
        ge=1,
        description="Preferred lifetime v sekundach - cas pocas ktoreho je adresa preferovana"
    )
    valid_lifetime: Optional[int] = Field(
        None,
        ge=1,
        description="Valid lifetime v sekundach - po expiraci sa adresa uvolni z poolu"
    )
