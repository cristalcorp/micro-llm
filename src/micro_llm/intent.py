"""Intention structurée : la seule sortie que le modèle a le droit de produire (D-007).

Le schéma est volontairement étroit. Ce qui n'y figure pas ne peut pas être demandé
au code d'assemblage : un conteneur privilégié, le socket Docker ou le réseau de l'hôte
n'ont pas de champ (état dangereux non représentable). Quand la demande en contient,
le modèle le signale dans `policy_flags`, et c'est l'utilisateur qui tranche (D-009).
"""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

ServiceName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,62}$")]

# Image d'un service hors catalogue : nom OCI simple, sans tag ni digest.
# L'épinglage par digest est fait par le code, jamais par le modèle.
ImageRef = Annotated[
    str, StringConstraints(pattern=r"^[a-z0-9]+([._/-][a-z0-9]+)*$", max_length=128)
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
    persistent: bool = False
    needs_internet: bool = False
    depends_on: tuple[ServiceName, ...] = ()

    @model_validator(mode="after")
    def _image_only_for_custom(self) -> Self:
        if self.kind is ServiceKind.CUSTOM and self.image is None:
            raise ValueError("un service 'custom' doit préciser son image")
        if self.kind is not ServiceKind.CUSTOM and self.image is not None:
            raise ValueError("l'image d'une brique du catalogue est fixée par le code")
        if self.name in self.depends_on:
            raise ValueError(f"'{self.name}' dépend de lui-même")
        return self


class Intent(_Strict):
    services: tuple[Service, ...] = Field(min_length=1, max_length=20)
    policy_flags: frozenset[PolicyFlag] = frozenset()

    @model_validator(mode="after")
    def _consistent_graph(self) -> Self:
        names = [s.name for s in self.services]
        duplicates = {n for n in names if names.count(n) > 1}
        if duplicates:
            raise ValueError(f"noms de services en double : {sorted(duplicates)}")
        known = set(names)
        for service in self.services:
            unknown = set(service.depends_on) - known
            if unknown:
                raise ValueError(
                    f"'{service.name}' dépend de services inconnus : {sorted(unknown)}"
                )
        _reject_cycles({s.name: s.depends_on for s in self.services})
        return self


def _reject_cycles(graph: dict[str, tuple[str, ...]]) -> None:
    done: set[str] = set()
    in_progress: set[str] = set()

    def visit(node: str) -> None:
        if node in done:
            return
        if node in in_progress:
            raise ValueError(f"dépendance circulaire autour de '{node}'")
        in_progress.add(node)
        for dep in graph[node]:
            visit(dep)
        in_progress.remove(node)
        done.add(node)

    for node in graph:
        visit(node)
