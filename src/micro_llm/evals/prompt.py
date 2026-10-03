"""Prompt de base, sans affinage (D-005), et extraction du JSON produit.

L'exemple du prompt est inventé et absent d'`evals/` : le jeu d'évaluation ne sert
jamais à mettre au point le modèle (D-009).
"""

import json
import re
from typing import Any

from micro_llm.intent import Exposure, PolicyFlag, ServiceKind

_EXAMPLE_REQUEST = "Mon outil ghcr.io/acme/notes, ouvert sur mon réseau, avec sa base MariaDB."
_EXAMPLE_INTENT: dict[str, object] = {
    "services": [
        {
            "name": "notes",
            "kind": "custom",
            "image": "ghcr.io/acme/notes",
            "exposure": "public",
            "persistent": False,
            "needs_internet": False,
            "depends_on": ["mariadb"],
            "policy_flags": [],
        },
        {
            "name": "mariadb",
            "kind": "mariadb",
            "exposure": "internal",
            "persistent": True,
            "needs_internet": False,
            "depends_on": [],
            "policy_flags": [],
        },
    ]
}


def _values(enum: type[ServiceKind] | type[Exposure] | type[PolicyFlag]) -> str:
    return ", ".join(m.value for m in enum)


SYSTEM_PROMPT = f"""\
Tu traduis une demande en français (ou en anglais) en une intention JSON décrivant des \
services Docker. Réponds uniquement avec le JSON, sans texte autour.

Format : {{"services": [<service>, ...]}}. Chaque service a :
- name : nom court en minuscules (lettres, chiffres, tirets).
- kind : {_values(ServiceKind)}. "custom" seulement pour une image hors de cette liste.
- image : seulement pour kind "custom", l'image citée sans tag (ex. ghcr.io/acme/app).
- exposure : "localhost" si la demande dit ma machine, mon poste, en local ; "public" si \
elle parle d'Internet, du réseau ou du public ; sinon "internal". Derrière un reverse \
proxy, seul le proxy est "public".
- persistent : true pour une base de données ou une appli qui garde des données, sauf \
demande jetable ou de démo ; Redis en cache : false ; Redis en file de tâches : true.
- needs_internet : true seulement si la demande dit que le service appelle l'extérieur \
(API externe, envoi de mails).
- depends_on : noms des services que celui-ci utilise ; le proxy dépend de ce qu'il sert.
- policy_flags : demandes dangereuses du service ({_values(PolicyFlag)}), sinon [].

Exemple. Demande : {_EXAMPLE_REQUEST}
Réponse : {json.dumps(_EXAMPLE_INTENT, ensure_ascii=False)}
"""


def build_messages(request: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": request},
    ]


_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


def extract_json(text: str) -> Any:
    """Renvoie l'objet JSON de la réponse, ou None s'il n'y en a pas de lisible.

    Tolère un bloc de réflexion `<think>` et des balises de code autour du JSON.
    """
    text = _THINK.sub("", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
