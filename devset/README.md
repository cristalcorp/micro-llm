# Jeu de mise au point : demande → intention

`intent_dev.jsonl` : 100 demandes **entièrement fictives** (images `ghcr.io/acme/*`), au même format et avec les mêmes conventions que `evals/` (voir `evals/README.md`) : 35 simples, 30 multi-services, 20 difficiles, 15 contraires à la politique.

**Rôle** : écrire et corriger les règles de la cascade (D-016) et, plus tard, valider les classifieurs. Les 30 cas d'`evals/` restent réservés à la mesure : on n'ajuste jamais une règle en regardant un échec d'`evals/` (D-009). Un test vérifie qu'aucune demande n'est commune aux deux jeux.

Mesure :

```sh
uv run python -m micro_llm.evals.run_rules --cases devset/intent_dev.jsonl
uv run python -m micro_llm.evals.run_rules --cases evals/intent_cases.jsonl
```
