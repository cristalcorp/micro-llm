"""Intention structurée : la seule sortie que le modèle a le droit de produire (D-007).

Le schéma est volontairement étroit. Ce qui n'y figure pas ne peut pas être demandé
au code d'assemblage : un conteneur privilégié, le socket Docker ou le réseau de l'hôte
n'ont pas de champ (état dangereux non représentable). Quand la demande en contient,
le modèle le signale dans les `policy_flags` du service concerné, et c'est
l'utilisateur qui tranche (D-009).
"""

from collections import Counter
from enum import StrEnum
from graphlib import CycleError, TopologicalSorter
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    model_validator,
)

# Nom DNS valide : pas de tiret final ni de « -- », 63 caractères au plus.
ServiceName = Annotated[str, StringConstraints(pattern=r"^[a-z](?:-?[a-z0-9])*$", max_length=63)]

# Image d'un service hors catalogue : nom OCI sans tag ni digest.
# L'épinglage par digest est fait par le code, jamais par le modèle.
_OCI_COMPONENT = r"[a-z0-9]+(?:(?:\.|_|__|-+)[a-z0-9]+)*"
ImageRef = Annotated[
    str,
    StringConstraints(pattern=rf"^{_OCI_COMPONENT}(?:/{_OCI_COMPONENT})*$", max_length=128),
]


class ServiceKind(StrEnum):
    """Briques du catalogue, chacune durcie par le code (D-006)."""

    POSTGRES = "postgres"
    MARIADB = "mariadb"
    REDIS = "redis"
    NGINX = "nginx"
    CADDY = "caddy"
    WORDPRESS = "wordpress"
    NEXTCLOUD = "nextcloud"
    GITEA = "gitea"
    CUSTOM = "custom"


# Une image « custom » ne doit pas contourner une brique durcie du catalogue,
# ni embarquer Docker lui-même.
_RESERVED_IMAGE_NAMES = {k.value for k in ServiceKind if k is not ServiceKind.CUSTOM} | {
    "postgresql",
    "mysql",
    "docker",
    "dind",
}


class Exposure(StrEnum):
    """Qui peut joindre le service."""

    INTERNAL = "internal"  # seulement les autres services du compose
    LOCALHOST = "localhost"  # la machine hôte, via 127.0.0.1
    PUBLIC = "public"  # le réseau, donc potentiellement Internet


class PolicyFlag(StrEnum):
    """Demandes contraires à la politique, non représentables dans le schéma."""

    PRIVILEGED = "privileged"
    DOCKER_SOCKET = "docker_socket"
    HOST_NETWORK = "host_network"
    HOST_FILESYSTEM = "host_filesystem"
    RUN_AS_ROOT = "run_as_root"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Service(_Strict):
    name: ServiceName
    kind: ServiceKind
    image: ImageRef | None = None
    exposure: Exposure = Exposure.INTERNAL
    persistent: StrictBool = False
    # Besoin propre à l'application ; ceux d'une brique (ACME de Caddy) sont ajoutés par le code.
    needs_internet: StrictBool = False
    depends_on: tuple[ServiceName, ...] = ()
    policy_flags: frozenset[PolicyFlag] = frozenset()

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.kind is ServiceKind.CUSTOM:
            if self.image is None:
                raise ValueError("un service 'custom' doit préciser son image")
            if self.image.rsplit("/", 1)[-1] in _RESERVED_IMAGE_NAMES:
                raise ValueError(
                    f"l'image '{self.image}' est réservée : utiliser la brique du catalogue"
                )
        elif self.image is not None:
            raise ValueError("l'image d'une brique du catalogue est fixée par le code")
        repeated = sorted(n for n, c in Counter(self.depends_on).items() if c > 1)
        if repeated:
            raise ValueError(f"'{self.name}' : dépendances répétées {repeated}")
        return self


class Intent(_Strict):
    services: tuple[Service, ...] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def _consistent_graph(self) -> Self:
        duplicates = sorted(n for n, c in Counter(s.name for s in self.services).items() if c > 1)
        if duplicates:
            raise ValueError(f"noms de services en double : {duplicates}")
        known = {s.name for s in self.services}
        for service in self.services:
            unknown = set(service.depends_on) - known
            if unknown:
                raise ValueError(
                    f"'{service.name}' dépend de services inconnus : {sorted(unknown)}"
                )
        try:
            TopologicalSorter({s.name: s.depends_on for s in self.services}).prepare()
        except CycleError as exc:
            cycle: list[str] = exc.args[1]
            raise ValueError(f"dépendance circulaire : {' -> '.join(cycle)}") from exc
        return self

    @property
    def policy_flags(self) -> frozenset[PolicyFlag]:
        flags: frozenset[PolicyFlag] = frozenset()
        return flags.union(*(s.policy_flags for s in self.services))
