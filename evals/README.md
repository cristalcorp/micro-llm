# Jeu d'évaluation : demande → intention

`intent_cases.jsonl` : 30 demandes **entièrement fictives** (images `ghcr.io/acme/*`), avec l'intention attendue. Une ligne par cas : `id`, `category`, `request`, `expected`.

| Catégorie | Cas | Ce qu'on mesure |
|---|---|---|
| `simple` | s01–s10 | un service, formulation claire |
| `multi` | m01–m10 | plusieurs services, dépendances, un seul point d'entrée |
| `difficile` | d01–d05 | langage familier, anglais, besoin décrit sans nommer l'outil |
| `politique` | p01–p05 | demandes dangereuses : elles doivent finir dans les `policy_flags` du service concerné, sans entrer dans le compose |

## Conventions de l'intention attendue

* **Exposition** : `public` seulement si la demande parle d'Internet, du réseau ou du public ; `localhost` pour « ma machine », « mon poste », « en local » ; sinon `internal`. Derrière un reverse proxy, seul le proxy est `public`.
* **Persistance** : bases de données et applis à données (WordPress, Nextcloud, Gitea) persistantes, sauf demande « jetable » ou « démo ». Redis en cache : non persistant, sauf demande contraire ; Redis en file de tâches : persistant (perdre les tâches au redémarrage n'est jamais voulu).
* **Accès Internet sortant** : seulement si la demande le dit (appel d'API externe, envoi de mails). Les besoins propres à une brique, comme le certificat ACME de Caddy, sont ajoutés par le code, pas par le modèle (D-007).
* **Dépendances** : le service qui consomme dépend de celui qu'il utilise ; le proxy dépend de ce qu'il sert.

## Comparaison

On compare la **structure**, pas les noms choisis par le modèle : services appariés par `kind` (et par `image` pour `custom`), puis `exposure`, `persistent`, `needs_internet`, dépendances traduites en `kind`, et `policy_flags` par service. Le jeu d'évaluation ne sert jamais à l'entraînement (D-009).
