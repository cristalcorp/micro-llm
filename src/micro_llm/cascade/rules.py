"""Premier étage de la cascade (D-016) : comprendre la demande avec des règles fixes.

Le texte est découpé autour des mentions de services ; chaque indice (exposition,
persistance, accès sortant, demande dangereuse) s'applique au service dont il suit la
mention, sinon au point d'entrée de la pile. Ce que les règles ne savent pas trancher
est listé dans `doubts`, pour l'étage suivant ou pour le questionnaire.

Mis au point sur `devset/` uniquement, jamais sur `evals/` (D-009).
"""

import re
import unicodedata
from dataclasses import dataclass, field

from micro_llm.intent import Exposure, Intent, PolicyFlag, Service, ServiceKind

K = ServiceKind

# Noms et descriptions qui désignent une brique du catalogue. Les descriptions
# (sans nom de produit) sont signalées en doute.
_NAMED: dict[ServiceKind, str] = {
    K.POSTGRES: r"postgres(?:ql)?",
    K.MARIADB: r"mariadb|mysql",
    K.REDIS: r"redis",
    K.NGINX: r"nginx",
    K.CADDY: r"caddy",
    K.WORDPRESS: r"wordpress",
    K.NEXTCLOUD: r"nextcloud",
    K.GITEA: r"gitea",
}
_DESCRIBED: dict[ServiceKind, str] = {
    K.REDIS: r"cache|cles?-valeurs?|key-value",
    K.NGINX: r"serveur web|web server|fichiers statiques|site statique",
    K.CADDY: r"https automatique|certificats",
    K.WORDPRESS: r"blog",
    K.NEXTCLOUD: r"dropbox|cloud perso|partager mes fichiers",
    K.GITEA: r"depots? git|git server|serveur git|comme github",
}
_GENERIC_PROXY = r"reverse proxy|proxy|frontal"
_GENERIC_DB = r"base de donnees|base|bdd|database|db"

_PROXIES = {K.NGINX, K.CADDY}
_DATABASES = {K.POSTGRES, K.MARIADB}
_DATA_APPS = {K.WORDPRESS, K.NEXTCLOUD, K.GITEA}

# Mots qui renvoient au service principal ou à la base déjà cités (« l'API est publique »).
_APP_REFS = r"l'api|the api|l'appli|l'app|the app|my app|mon app\w*|le site|l'interface"
_DB_REFS = r"la base|la bdd|the db|the database"

_IMAGE = re.compile(r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:/[a-z0-9._-]*[a-z0-9])+")

_OUTBOUND = (
    r"api externe|external api|envoy\w*[^.;]*?(?:mails?|sms)|envoi\w*[^.;]*?(?:mails?|sms)"
    r"|par mail|telecharg\w*[^.;]*?internet|appel\w* une api|call an? external"
)
_PUBLIC = (
    r"publi(?:c|qu)\w*|internet|reseau|network|ouvert\w*|open|visible\w*|equipe|team"
    r"|tout le monde|clients?|entreprise|bureau|expose\w*|accessible depuis l'exterieur"
)
_LOCAL = (
    r"en local|locale?s?|localement|ma machine|mon poste|mon pc|ce pc|mon ordinateur"
    r"|mon laptop|mon portable|laptop|my machine|my computer|localhost"
)
_INTERNAL = (
    r"interne?s?|internal|conteneurs|containers|compose|personne d'autre|other services"
    r"|cachee?|rien d'expose|pas d'exposition|pas d'acces exterieur"
)
_NEGATION = r"\b(?:pas|sans|not|ne|n'|aucun|rien|jamais|personne)\b[^,.;]{0,25}$"
_ALL = r"tous (?:les )?deux|toutes (?:les )?deux|both"

_EPHEMERAL = (
    r"jetable|demo|jett?e|jeter|supprimer\w*|rien a garder|peut tout perdre"
    r"|pas besoin de garder|throwaway|temporaire|temporary"
)
_PERSISTENT = r"persist\w*|garde\w*|conserv\w*|survi\w*|redemarrage|ne perd rien|pas perdre"
# Redis utilisé comme file : ses tâches ne doivent pas se perdre (evals/README.md).
_QUEUE = r"file|taches|queue|workers|messages"

_FLAGS: dict[PolicyFlag, str] = {
    PolicyFlag.DOCKER_SOCKET: r"docker\.sock|socket docker|docker socket|pilote docker",
    PolicyFlag.PRIVILEGED: r"privileg\w*",
    PolicyFlag.HOST_NETWORK: r"reseau de l'hote|host network|network_mode host",
    PolicyFlag.RUN_AS_ROOT: r"(?:en|as|tant que|utilisateur) root",
    PolicyFlag.HOST_FILESYSTEM: (
        r"(?<![\w.])/(?:home|var(?!/run/docker)|etc|root|srv|mnt|opt|usr)\b"
        r"|monte (?:la racine )?/(?!\w)|racine"
        r"|tout le disque|disque de la machine|dossier personnel"
    ),
}


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower().replace("’", "'"))
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _find(pattern: str, text: str) -> list[re.Match[str]]:
    return list(re.finditer(rf"(?<![\w-])(?:{pattern})(?![\w-])", text))


@dataclass
class _Draft:
    kind: ServiceKind
    image: str | None = None
    segments: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.image.rsplit("/", 1)[-1] if self.image else self.kind.value


@dataclass(frozen=True)
class RulesResult:
    intent: Intent
    doubts: tuple[str, ...]


def _exposure_cue(segment: str) -> Exposure | None:
    for match in _find(_PUBLIC, segment):
        if re.search(_NEGATION, segment[: match.start()]):
            return Exposure.INTERNAL
    if _find(_INTERNAL, segment):
        return Exposure.INTERNAL
    if _find(_LOCAL, segment) and not re.search(r"reseau local", segment):
        return Exposure.LOCALHOST
    if _find(_PUBLIC, segment):
        return Exposure.PUBLIC
    return None


def parse(request: str) -> RulesResult:
    text = _normalize(request)
    doubts: list[str] = []

    # 1. Images explicites, masquées ensuite pour ne pas lire « blog » dans …/blog.
    drafts: list[_Draft] = []
    mentions: list[tuple[int, int, _Draft]] = []
    for match in _IMAGE.finditer(text):
        draft = _Draft(K.CUSTOM, image=match.group())
        drafts.append(draft)
        mentions.append((match.start(), match.end(), draft))
    masked = _IMAGE.sub(lambda m: " " * len(m.group()), text)

    # 2. Briques nommées, puis décrites.
    by_kind: dict[ServiceKind, _Draft] = {}

    def mention(kind: ServiceKind, match: re.Match[str]) -> None:
        if kind not in by_kind:
            by_kind[kind] = _Draft(kind)
            drafts.append(by_kind[kind])
        mentions.append((match.start(), match.end(), by_kind[kind]))

    for kind, pattern in _NAMED.items():
        for match in _find(pattern, masked):
            mention(kind, match)
    for kind, pattern in _DESCRIBED.items():
        matches = _find(pattern, masked)
        if matches and kind not in by_kind:
            doubts.append(f"{kind.value} : déduit d'une description")
        for match in matches:
            mention(kind, match)
    proxies = _find(_GENERIC_PROXY, masked)
    if proxies:
        known = [k for k in by_kind if k in _PROXIES]
        kind = known[0] if known else K.NGINX
        if not known:
            doubts.append(f"{kind.value} : proxy sans nom de produit")
        for match in proxies:
            mention(kind, match)
    generic_db = [
        m
        for m in _find(_GENERIC_DB, masked)
        if not re.search(r"(?:^|\s)a $", masked[: m.start()])  # « à base de redis »
    ]
    if generic_db and not by_kind.keys() & _DATABASES:
        kind = K.MARIADB if K.WORDPRESS in by_kind else K.POSTGRES
        doubts.append(f"{kind.value} : base de données sans nom de produit")
        for match in generic_db:
            mention(kind, match)

    if not drafts:
        raise ValueError("aucun service reconnu")

    # 3. Rôles.
    apps = [d for d in drafts if d.kind not in _DATABASES and d.kind is not K.REDIS]
    served = [d for d in apps if d.kind not in _PROXIES]
    proxies_d = [d for d in apps if d.kind in _PROXIES] if served else []
    entry = proxies_d or apps or drafts
    main = (served or apps or drafts)[0]
    databases = [d for d in drafts if d.kind in _DATABASES]

    # Renvois (« l'API », « la base ») = mentions du service principal ou de la base.
    for match in _find(_APP_REFS, masked):
        mentions.append((match.start(), match.end(), main))
    if databases:
        for match in _find(_DB_REFS, masked):
            mentions.append((match.start(), match.end(), databases[0]))

    # 4. Segments : du bout d'une mention au début de la suivante.
    mentions.sort(key=lambda m: m[0])
    preamble = masked[: mentions[0][0]]
    for i, (_, end, draft) in enumerate(mentions):
        stop = mentions[i + 1][0] if i + 1 < len(mentions) else len(masked)
        draft.segments.append(masked[end:stop])

    def exposure_text(segment: str) -> str:
        # Ni l'accès sortant ni le réseau de l'hôte ne disent qui peut joindre le service.
        without = re.sub(_OUTBOUND, " ", segment)
        return re.sub(_FLAGS[PolicyFlag.HOST_NETWORK], " ", without)

    def own(draft: _Draft, pattern: str) -> bool:
        return any(_find(pattern, s) for s in draft.segments)

    # 5. Exposition.
    global_cue = _exposure_cue(exposure_text(preamble))
    to_all = bool(_find(_ALL, masked))
    exposures: dict[int, Exposure] = {}
    for draft in drafts:
        cues = [_exposure_cue(exposure_text(s)) for s in draft.segments]
        cue = next((c for c in cues if c is not None), None)
        if cue is None and (draft in entry or to_all):
            cue = global_cue
        exposures[id(draft)] = cue or Exposure.INTERNAL
    if to_all:
        shared = next((exposures[id(d)] for d in drafts if exposures[id(d)] != "internal"), None)
        for draft in drafts:
            exposures[id(draft)] = shared or exposures[id(draft)]
    if _exposure_cue(exposure_text(masked)) is None:
        doubts.append("exposition : aucun indice, interne par défaut")

    # 6. Persistance.
    ephemeral = bool(_find(_EPHEMERAL, masked))
    persistent: dict[int, bool] = {}
    for draft in drafts:
        if ephemeral:
            value = False
        elif draft.kind is K.REDIS:
            value = not own(draft, r"cache") and (
                own(draft, _PERSISTENT)
                or bool(_find(_QUEUE, masked))
                or (len(drafts) == 1 and bool(_find(_PERSISTENT, masked)))
            )
        elif draft.kind in _DATABASES or draft.kind in _DATA_APPS:
            value = True
        else:
            value = own(draft, _PERSISTENT)
        persistent[id(draft)] = value

    # 7. Accès sortant et demandes dangereuses : au service dont ils suivent la mention.
    def owner(pattern: str) -> list[_Draft]:
        found = [d for d in drafts if own(d, pattern)]
        return found or ([main] if _find(pattern, masked) else [])

    internet = {id(d) for d in owner(_OUTBOUND)}
    if any(d.kind in _DATABASES or d.kind is K.REDIS for d in drafts if id(d) in internet):
        internet = {id(main)}
    flags: dict[int, set[PolicyFlag]] = {id(d): set() for d in drafts}
    for flag, pattern in _FLAGS.items():
        for draft in owner(pattern):
            flags[id(draft)].add(flag)

    # 8. Dépendances par rôle.
    stores = [d for d in drafts if d.kind in _DATABASES or d.kind is K.REDIS]
    depends: dict[int, list[str]] = {id(d): [] for d in drafts}
    for draft in served:
        if draft.kind in _PROXIES:
            continue
        depends[id(draft)] = [s.name for s in stores]
    for proxy in proxies_d:
        depends[id(proxy)] = [d.name for d in served]

    services = tuple(
        Service(
            name=d.name,
            kind=d.kind,
            image=d.image,
            exposure=exposures[id(d)],
            persistent=persistent[id(d)],
            needs_internet=id(d) in internet,
            depends_on=tuple(depends[id(d)]),
            policy_flags=frozenset(flags[id(d)]),
        )
        for d in drafts
    )
    return RulesResult(Intent(services=services), tuple(doubts))
