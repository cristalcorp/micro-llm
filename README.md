# micro-llm

Mini-applications ultra-spécialisées qui font tourner un petit modèle de langage **100 % en local**. Aucune donnée ne quitte la machine.

## Première application : demande en français → `docker-compose.yml` sûr par défaut

```
demande ─▶ questionnaire (code) ─▶ intention JSON (modèle) ─▶ confirmation ─▶ assemblage (code) ─▶ durcissement + scans (code) ─▶ compose + rapport
```

* **L'IA seulement là où elle est indispensable** : le modèle ne fait que comprendre la demande et produire une intention JSON validée par un schéma. Tout le reste est du code déterministe.
* **La sécurité ne dépend pas du modèle** : images minimales épinglées par digest, `read_only`, `cap_drop: [ALL]`, `no-new-privileges`, utilisateur non-root, réseaux internes, scan des secrets et des vulnérabilités, `dependabot.yml` généré.
* **Les règles ne s'assouplissent jamais d'elles-mêmes** : toute exception est humaine, explicite et écrite dans le fichier.

État : squelette, rien n'est encore fonctionnel.

## Développement

```sh
uv sync
uv run ruff check .
uv run mypy src tests
uv run pytest
```

## Licence

[AGPL-3.0](LICENSE).
