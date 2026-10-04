"""Génération de données d'entraînement fictives, étiquetées par construction (D-018).

On tire d'abord une intention conforme aux conventions d'`evals/README.md`, puis on
rédige une demande qui la décrit. L'étiquette ne dépend donc jamais d'un modèle.
Les phénomènes que les règles ratent (renvois, négations, briques décrites, relations,
indice éloigné de son service) sont produits exprès et notés dans `phenomena`.

    uv run python -m micro_llm.cascade.datagen --n 5000 --seed 0 --out train.jsonl
"""

import argparse
import json
import random
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from micro_llm.intent import Exposure, Intent, PolicyFlag, ServiceKind

K = ServiceKind
E = Exposure
F = PolicyFlag

# --- Formulations (fictives, écrites à la main) -----------------------------------

_NAMED_FR: dict[ServiceKind, list[str]] = {
    K.POSTGRES: ["un Postgres", "une base PostgreSQL", "une bdd postgres", "un PostgreSQL"],
    K.MARIADB: ["une MariaDB", "une base MariaDB", "un MySQL", "une base mysql"],
    K.REDIS: ["un Redis", "une instance Redis", "un redis"],
    K.NGINX: ["un nginx", "un serveur Nginx", "un NGINX"],
    K.CADDY: ["un Caddy", "un serveur Caddy", "un caddy"],
    K.WORDPRESS: ["un WordPress", "un site WordPress", "un wordpress"],
    K.NEXTCLOUD: ["un Nextcloud", "une instance Nextcloud", "un nextcloud"],
    K.GITEA: ["un Gitea", "un serveur Gitea", "un gitea"],
}
_NAMED_EN: dict[ServiceKind, list[str]] = {
    K.POSTGRES: ["a Postgres database", "a PostgreSQL instance"],
    K.MARIADB: ["a MariaDB database", "a MySQL database"],
    K.REDIS: ["a Redis instance", "Redis"],
    K.NGINX: ["an nginx server", "Nginx"],
    K.CADDY: ["a Caddy server", "Caddy"],
    K.WORDPRESS: ["a WordPress site", "WordPress"],
    K.NEXTCLOUD: ["a Nextcloud instance", "Nextcloud"],
    K.GITEA: ["a Gitea server", "Gitea"],
}
# Briques décrites sans leur nom : le cœur du travail de l'encodeur.
_DESCRIBED_FR: dict[ServiceKind, list[str]] = {
    K.REDIS: ["un stockage clé-valeur en mémoire", "un truc pour mettre en cache"],
    K.NGINX: ["un serveur HTTP tout simple", "un serveur pour des pages statiques"],
    K.CADDY: ["un frontal qui gère le HTTPS tout seul", "un proxy avec certificats automatiques"],
    K.WORDPRESS: ["un CMS pour mon site", "un blog facile à éditer"],
    K.NEXTCLOUD: [
        "un équivalent de Google Drive chez moi",
        "un espace pour synchroniser mes fichiers",
    ],
    K.GITEA: ["une forge git perso", "un endroit pour héberger mon code comme sur GitLab"],
}
_DESCRIBED_EN: dict[ServiceKind, list[str]] = {
    K.REDIS: ["an in-memory key-value store"],
    K.NGINX: ["a plain static web server"],
    K.CADDY: ["a reverse proxy with automatic HTTPS"],
    K.WORDPRESS: ["an easy CMS for my site"],
    K.NEXTCLOUD: ["a self-hosted Google Drive"],
    K.GITEA: ["a self-hosted git forge"],
}
_CUSTOM_FR = ["l'image {img}", "mon appli {img}", "mon API {img}", "le conteneur {img}", "{img}"]
_CUSTOM_EN = ["the image {img}", "my app {img}", "my API {img}", "{img}"]
_CUSTOM_NAMES = [
    "invoice", "ledger", "chat", "kiosk", "metrics", "planner", "mapper", "quiz", "booking",
    "inventory", "survey", "tickets", "gateway", "uploader", "crm-lite", "pricing", "feeds",
    "recipes", "payroll", "studio", "courier", "ranker", "atlas", "pulse", "relay", "vault-ui",
]  # fmt: skip
_REF_FR: dict[ServiceKind, list[str]] = {
    K.CUSTOM: ["l'appli", "le service", "l'API"],
    K.WORDPRESS: ["le site", "le blog"],
    K.NEXTCLOUD: ["le cloud", "l'appli"],
    K.GITEA: ["la forge", "le serveur git"],
}
_DB_REF_FR = ["la base", "la bdd"]

_EXPO_FR: dict[Exposure, list[str]] = {
    E.PUBLIC: [
        "accessible depuis Internet", "visible sur le web", "joignable depuis le réseau",
        "en accès public", "ouvert à toute l'équipe sur le réseau", "avec un accès public",
    ],
    E.LOCALHOST: [
        "accessible seulement depuis ma machine", "juste sur mon poste", "en local",
        "uniquement sur ce PC", "depuis mon laptop seulement",
    ],
    E.INTERNAL: [
        "sans accès extérieur", "réservé aux autres conteneurs", "en interne",
        "invisible de l'extérieur", "pas exposé",
    ],
}  # fmt: skip
_EXPO_EN: dict[Exposure, list[str]] = {
    E.PUBLIC: ["reachable from the internet", "open to the public", "exposed on the network"],
    E.LOCALHOST: ["only on my laptop", "reachable from my machine only", "local only"],
    E.INTERNAL: ["internal only", "not exposed", "only for the other containers"],
}
_COREF_FR: dict[Exposure, list[str]] = {
    E.PUBLIC: [
        "{ref} est accessible depuis Internet",
        "{ref} doit être accessible à tous",
        "{ref}, lui, est visible",
    ],
    E.LOCALHOST: ["{ref} reste sur mon poste", "{ref} juste en local"],
    E.INTERNAL: ["{ref} reste interne", "{ref} ne doit pas être exposé"],
}
_EPHEMERAL_FR = ["c'est pour une démo", "je jette tout après", "rien à garder", "c'est jetable"]
_EPHEMERAL_EN = ["it's a throwaway demo", "nothing needs to be kept"]
_CACHE_FR = ["pour le cache", "en cache", "comme cache"]
_QUEUE_FR = ["comme file de tâches", "pour les jobs de mes workers", "comme file de messages"]
_REDIS_KEEP_FR = ["qui garde ses données", "qui doit survivre aux redémarrages"]
_OUTBOUND_FR = [
    "qui doit appeler une API externe", "qui envoie des mails", "qui contacte un service tiers",
    "qui télécharge des données sur Internet", "qui envoie des notifications par SMS",
]  # fmt: skip
_OUTBOUND_EN = ["that calls an external API", "that sends emails"]
_FLAGS_FR: dict[PolicyFlag, list[str]] = {
    F.DOCKER_SOCKET: ["avec le socket Docker monté", "qui pilote le Docker de l'hôte"],
    F.PRIVILEGED: ["en mode privilégié", "avec --privileged"],
    F.HOST_NETWORK: ["sur le réseau de l'hôte", "en network_mode host"],
    F.HOST_FILESYSTEM: ["avec mon dossier /home monté", "qui lit tout le disque de l'hôte"],
    F.RUN_AS_ROOT: ["lancé en root", "qui tourne en tant que root"],
}
_FLAGS_EN: dict[PolicyFlag, list[str]] = {
    F.DOCKER_SOCKET: ["with the Docker socket mounted"],
    F.PRIVILEGED: ["in privileged mode"],
    F.HOST_NETWORK: ["on the host network"],
    F.HOST_FILESYSTEM: ["with the host's /home mounted"],
    F.RUN_AS_ROOT: ["running as root"],
}
_PREFIX_FR = ["", "Je veux ", "Il me faut ", "Monte-moi ", "J'aimerais ", "faut "]
_PREFIX_EN = ["", "I need ", "Set up ", "Spin up "]


# --- Intention tirée ----------------------------------------------------------------


@dataclass
class _Svc:
    kind: ServiceKind
    name: str
    image: str | None = None
    exposure: Exposure = E.INTERNAL
    persistent: bool = False
    internet: bool = False
    deps: list[str] = field(default_factory=list)
    flags: set[PolicyFlag] = field(default_factory=set)
    redis_role: str = ""  # cache | queue | keep
    text: list[str] = field(default_factory=list)  # morceaux de phrase propres au service

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "name": self.name,
            "kind": self.kind.value,
            "exposure": self.exposure.value,
            "persistent": self.persistent,
            "needs_internet": self.internet,
            "depends_on": self.deps,
            "policy_flags": sorted(f.value for f in self.flags),
        }
        if self.image:
            out["image"] = self.image
        return out


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


_DISPLAY = {
    K.POSTGRES: "Postgres", K.MARIADB: "MariaDB", K.REDIS: "Redis", K.NGINX: "nginx",
    K.CADDY: "Caddy", K.WORDPRESS: "WordPress", K.NEXTCLOUD: "Nextcloud", K.GITEA: "Gitea",
}  # fmt: skip


def _definite(svc: _Svc) -> str:
    """Nouvelle mention d'un service déjà cité : « le WordPress », « ghcr.io/acme/x »."""
    return svc.image or f"le {_DISPLAY[svc.kind]}"


class _Gen:
    def __init__(self, rng: random.Random) -> None:
        self.r = rng

    def pick[T](self, items: list[T]) -> T:
        return self.r.choice(items)

    def chance(self, p: float) -> bool:
        return self.r.random() < p

    def custom(self, used: set[str]) -> _Svc:
        name = self.pick([n for n in _CUSTOM_NAMES if n not in used])
        used.add(name)
        return _Svc(K.CUSTOM, name, image=f"ghcr.io/acme/{name}")

    def store(self, kind: ServiceKind) -> _Svc:
        svc = _Svc(kind, kind.value)
        if kind is K.REDIS:
            svc.redis_role = self.pick(["cache", "cache", "queue", "keep"])
        return svc

    def app(self, used: set[str]) -> _Svc:
        kind = self.pick([K.CUSTOM, K.CUSTOM, K.WORDPRESS, K.NEXTCLOUD, K.GITEA])
        return self.custom(used) if kind is K.CUSTOM else _Svc(kind, kind.value)

    def scenario(self) -> tuple[list[_Svc], list[_Svc], str]:
        """Renvoie (services, points d'entrée, forme)."""
        used: set[str] = set()
        shape = self.pick(
            ["single"] * 6 + ["app_store"] * 5 + ["proxy"] * 4 + ["chain", "worker", "both"]
        )
        if shape == "single":
            kind = self.pick(list(K))
            svc = self.custom(used) if kind is K.CUSTOM else self.store(kind)
            return [svc], [svc], shape
        if shape == "both":
            a, b = self.r.sample([K.POSTGRES, K.MARIADB, K.REDIS], 2)
            pair = [self.store(a), self.store(b)]
            for svc in pair:
                svc.redis_role = "cache" if svc.kind is K.REDIS else ""
            return pair, pair, shape
        if shape == "worker":
            worker, redis = self.custom(used), _Svc(K.REDIS, "redis", redis_role="queue")
            worker.deps = ["redis"]
            return [worker, redis], [worker], shape
        app = self.app(used)
        stores: list[_Svc] = []
        db = K.MARIADB if app.kind is K.WORDPRESS else self.pick([K.POSTGRES, K.MARIADB])
        if app.kind is not K.GITEA or self.chance(0.6):
            stores.append(self.store(db))
        if self.chance(0.35):
            stores.append(_Svc(K.REDIS, "redis", redis_role="cache"))
        app.deps = [s.name for s in stores]
        if shape == "chain":
            front = self.custom(used)
            app = app if app.kind is K.CUSTOM else self.custom(used)
            app.deps = [s.name for s in stores]
            front.deps = [app.name]
            return [front, app, *stores], [front], shape
        if shape == "proxy":
            proxy = _Svc(self.pick([K.NGINX, K.CADDY]), "")
            proxy.name = proxy.kind.value
            proxy.deps = [app.name]
            return [proxy, app, *stores], [proxy], shape
        return [app, *stores], [app], shape

    def label(self, services: list[_Svc], entry: list[_Svc], ephemeral: bool) -> None:
        exposure = self.pick([E.PUBLIC, E.PUBLIC, E.LOCALHOST, E.INTERNAL])
        for svc in services:
            svc.exposure = exposure if svc in entry else E.INTERNAL
            if ephemeral:
                svc.persistent = False
            elif svc.kind is K.REDIS:
                svc.persistent = svc.redis_role in ("queue", "keep")
            else:
                svc.persistent = svc.kind in {K.POSTGRES, K.MARIADB, K.WORDPRESS, K.NEXTCLOUD}
                svc.persistent |= svc.kind is K.GITEA


# --- Rédaction ---------------------------------------------------------------------


def _render(
    g: _Gen, services: list[_Svc], entry: list[_Svc], shape: str, ephemeral: bool
) -> tuple[str, set[str]]:
    english = g.chance(0.2)
    tags: set[str] = {"english"} if english else set()

    def noun(svc: _Svc) -> str:
        if svc.kind is K.CUSTOM:
            return g.pick(_CUSTOM_EN if english else _CUSTOM_FR).format(img=svc.image)
        described = (_DESCRIBED_EN if english else _DESCRIBED_FR).get(svc.kind)
        if described and g.chance(0.25):
            tags.add("described")
            return g.pick(described)
        return g.pick((_NAMED_EN if english else _NAMED_FR)[svc.kind])

    expo = _EXPO_EN if english else _EXPO_FR
    main = entry[0]
    coref = (
        not english
        and main.exposure is not E.INTERNAL
        and main.kind in _REF_FR
        and len(services) > 1
        and g.chance(0.4)
    )
    parts: list[str] = []
    for svc in services:
        bits = [noun(svc)]
        if svc in entry and not coref and (svc.exposure is not E.INTERNAL or g.chance(0.6)):
            bits.append(g.pick(expo[svc.exposure]))
        elif svc not in entry and svc.kind in {K.POSTGRES, K.MARIADB} and g.chance(0.3):
            if not english and main.exposure is E.PUBLIC and g.chance(0.5):
                bits.append("sans que " + g.pick(_DB_REF_FR) + " soit visible de l'extérieur")
                tags.add("negation")
            else:
                bits.append(g.pick(expo[E.INTERNAL]))
        if svc.kind is K.REDIS and not english:
            role = {"cache": _CACHE_FR, "queue": _QUEUE_FR, "keep": _REDIS_KEEP_FR}
            bits.append(g.pick(role[svc.redis_role]))
        elif svc.kind is K.REDIS:
            bits.append(
                {"cache": "as a cache", "queue": "as a job queue", "keep": "persistent"}[
                    svc.redis_role
                ]
            )
        if svc.internet:
            bits.append(g.pick(_OUTBOUND_EN if english else _OUTBOUND_FR))
        for flag in sorted(svc.flags):
            if svc.text == ["far"] and not english:
                continue
            bits.append(g.pick((_FLAGS_EN if english else _FLAGS_FR)[flag]))
        parts.append(" ".join(bits))

    links = {"proxy": (" devant ", " in front of "), "chain": (" qui appelle ", " calling ")}
    if shape in links and len(parts) >= 2:
        tags.add("relation")
        parts[:2] = [parts[0] + links[shape][english] + parts[1]]
    joiner = ", " if g.chance(0.6) else (" and " if english else " avec ")
    sentence = joiner.join(parts)
    if shape == "both" and services[0].exposure is not E.INTERNAL:
        tags.add("both")
        sentence = joiner.join(noun(s) for s in services)
        both = "both " if english else "tous les deux "
        sentence += ", " + both + g.pick(expo[services[0].exposure])
    if coref:
        tags.add("coref")
        ref = g.pick(_REF_FR[main.kind])
        sentence += ", " + g.pick(_COREF_FR[main.exposure]).format(ref=ref)
    for svc in services:
        if svc.text == ["far"] and not english:
            tags.add("far_flag")
            flag = next(iter(svc.flags))
            sentence += f", et je veux {_definite(svc)} {g.pick(_FLAGS_FR[flag])}"
    if ephemeral:
        sentence += ", " + g.pick(_EPHEMERAL_EN if english else _EPHEMERAL_FR)
    prefix = g.pick(_PREFIX_EN if english else _PREFIX_FR)
    text = prefix + sentence + g.pick([".", "", "."])
    if not english and g.chance(0.2):
        tags.add("familiar")
        text = text.lower()
        if g.chance(0.5):
            text = _strip_accents(text)
    return text[0].upper() + text[1:] if not tags & {"familiar"} else text, tags


def generate(n: int, seed: int) -> list[dict[str, object]]:
    # Graine fixe voulue : données reproductibles, aucun usage de sécurité.
    g = _Gen(random.Random(seed))  # nosec B311
    out: list[dict[str, object]] = []
    seen: set[str] = set()
    while len(out) < n:
        services, entry, shape = g.scenario()
        ephemeral = shape != "worker" and g.chance(0.15)
        g.label(services, entry, ephemeral)
        apps = [s for s in services if s.kind not in {K.POSTGRES, K.MARIADB, K.REDIS}]
        if apps and g.chance(0.2):
            g.pick([s for s in apps if s.kind not in {K.NGINX, K.CADDY}] or apps).internet = True
        if g.chance(0.18):
            target = g.pick(services)
            target.flags = set(g.r.sample(list(F), g.pick([1, 1, 1, 2])))
            # Indice éloigné de son service (« …, et monte / pour WordPress ») : 1 fois sur 4.
            if len(services) > 1 and len(target.flags) == 1 and g.chance(0.25):
                target.text = ["far"]
        text, tags = _render(g, services, entry, shape, ephemeral)
        if text in seen:
            continue
        seen.add(text)
        intent = {"services": [s.to_dict() for s in services]}
        Intent.model_validate(intent)
        out.append(
            {
                "id": f"g{len(out):05d}",
                "category": shape,
                "phenomena": sorted(tags),
                "request": text,
                "expected": intent,
            }
        )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Génère des demandes fictives étiquetées.")
    parser.add_argument("--n", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    rows = generate(args.n, args.seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"{len(rows)} demandes → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
