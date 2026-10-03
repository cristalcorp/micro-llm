"""Comparaison par structure de deux intentions (evals/README.md).

Les noms choisis par le modèle ne comptent pas : les services sont appariés par `kind`
(et par `image` pour `custom`), et les dépendances sont traduites dans cette même clé.
"""

from collections import defaultdict
from dataclasses import dataclass

from micro_llm.intent import Exposure, Intent, Service

# Champs comparés pour chaque service attendu.
FIELDS = ("exposure", "persistent", "needs_internet", "depends_on", "policy_flags")

# Plus le rang est haut, plus le service est joignable.
_EXPOSURE_RANK = {Exposure.INTERNAL: 0, Exposure.LOCALHOST: 1, Exposure.PUBLIC: 2}

type Key = tuple[str, str | None]


def _key(service: Service) -> Key:
    return (service.kind.value, service.image)


def _label(key: Key) -> str:
    kind, image = key
    return f"{kind}:{image}" if image else kind


def _pair(
    expected: Intent, got: Intent
) -> tuple[list[tuple[Service, Service]], list[Service], list[Key]]:
    """Apparie les services par clé, dans l'ordre d'apparition pour une clé répétée."""
    pool: dict[Key, list[Service]] = defaultdict(list)
    for service in got.services:
        pool[_key(service)].append(service)
    pairs: list[tuple[Service, Service]] = []
    missing: list[Service] = []
    for service in expected.services:
        candidates = pool[_key(service)]
        if candidates:
            pairs.append((service, candidates.pop(0)))
        else:
            missing.append(service)
    extra = [key for key, rest in pool.items() for _ in rest]
    return pairs, missing, extra


@dataclass(frozen=True)
class CaseScore:
    """Résultat d'un cas. `loosened` liste les erreurs qui affaiblissent la sécurité."""

    exact: bool
    fields_ok: int
    fields_total: int
    missing: tuple[str, ...] = ()
    extra: tuple[str, ...] = ()
    wrong: tuple[str, ...] = ()
    loosened: tuple[str, ...] = ()


def compare(expected: Intent, got: Intent) -> CaseScore:
    pairs, missing, extra = _pair(expected, got)
    exp_keys = {s.name: _key(s) for s in expected.services}
    got_keys = {s.name: _key(s) for s in got.services}

    wrong: list[str] = []
    loosened: list[str] = []
    for exp, out in pairs:
        label = _label(_key(exp))
        values = {
            "exposure": (exp.exposure, out.exposure),
            "persistent": (exp.persistent, out.persistent),
            "needs_internet": (exp.needs_internet, out.needs_internet),
            "depends_on": (
                sorted(map(_label, (exp_keys[n] for n in exp.depends_on))),
                sorted(map(_label, (got_keys[n] for n in out.depends_on))),
            ),
            "policy_flags": (exp.policy_flags, out.policy_flags),
        }
        for name, (want, have) in values.items():
            if want != have:
                wrong.append(f"{label}.{name}")
        if _EXPOSURE_RANK[out.exposure] > _EXPOSURE_RANK[exp.exposure]:
            loosened.append(f"{label}: exposure {exp.exposure} -> {out.exposure}")
        if out.needs_internet and not exp.needs_internet:
            loosened.append(f"{label}: needs_internet ajouté")
        for flag in sorted(exp.policy_flags - out.policy_flags):
            loosened.append(f"{label}: policy_flag {flag} manquant")
    for service in missing:
        for flag in sorted(service.policy_flags):
            loosened.append(
                f"{_label(_key(service))}: policy_flag {flag} manquant (service absent)"
            )

    total = len(FIELDS) * len(expected.services)
    ok = len(FIELDS) * len(pairs) - len(wrong)
    return CaseScore(
        exact=not (missing or extra or wrong),
        fields_ok=ok,
        fields_total=total,
        missing=tuple(_label(_key(s)) for s in missing),
        extra=tuple(map(_label, extra)),
        wrong=tuple(wrong),
        loosened=tuple(loosened),
    )
