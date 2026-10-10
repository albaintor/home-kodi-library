# API REST v1

Préfixe `/api/v1`. JSON UTF-8, import CLZ en multipart et images binaires. Les opérations de catalogue et de lecture restent disponibles sans front ni Home Assistant.

## Authentification

Un client natif utilise `Authorization: Bearer <API_TOKEN>`. `API_READ_TOKEN` donne accès aux lectures du catalogue, images, sources expurgées, cibles et tâches, sans modification, import ni lancement de lecture. Les jetons sont configurés au démarrage et doivent être aléatoires et distincts (minimum 24 caractères). Il n'existe pas encore d'utilisateurs multiples ni d'OAuth.

Le navigateur échange le jeton via `POST /auth/session` contre un cookie opaque HttpOnly, SameSite=Lax, valable 12 h. `GET /auth/session` retourne le rôle et le jeton CSRF ; les écritures avec cookie requièrent `X-CSRF-Token`. `DELETE /auth/session` ferme la session. Les jetons Bearer ne requièrent ni cookie ni CSRF. Les sessions ne survivent pas au redémarrage ; les jetons Bearer restent valables.

Ingress transmet un secret distinct dans `X-Kodi-Library-Ingress-Token`. Un simple `X-Ingress-Path` ne donne aucun accès. CORS est désactivé par défaut et inutile pour `URLSession` ; `CORS_ORIGINS` accepte une liste JSON d'origines web explicites.

## Routes

| Méthode | Route après `/api/v1` | Résultat |
|---|---|---|
| GET | `/status` | Compteurs, versions, état des sources |
| GET | `/media` | Page du catalogue et total |
| GET | `/media/{id}` | Fiche, identifiants, éditions, métadonnées par source |
| GET | `/media/{id}/artwork/{poster\|fanart}` | Image protégée et mise en cache |
| GET | `/facets` | Genres, formats, collections, années, sources |
| GET/POST | `/sources` | Liste / création d'une source |
| PUT/DELETE | `/sources/{id}` | Modification / retrait de la source et de ses éditions |
| POST | `/sources/{id}/sync` | Synchronisation Kodi, tâche retournée avec HTTP 202 |
| POST | `/imports/clz` | XML multipart, champs `source_id` et `file`, HTTP 202 |
| GET | `/jobs/{id}` | `pending`, `running`, `completed` ou `failed` |
| GET/POST | `/players` | Liste / création d'une cible Kodi |
| PUT/DELETE | `/players/{id}` | Modification / retrait d'une cible |
| POST | `/players/{id}/play` | Lecture d'un média, édition facultative et reprise |
| GET | `/openapi.json` | Contrat OpenAPI typé, authentification requise |

`/healthz` expose seulement l'état du processus sans authentification. `/docs` est protégé et expose Swagger UI ; ses fichiers Swagger proviennent du CDN par défaut de FastAPI.

## Recherche, filtres et pagination

`GET /media` accepte : `q`, `kind` (`movie`, `tvshow`, `episode`), `watch_status` (`watched`, `unwatched`, `in_progress`, `unknown`), `genre`, `format`, `collection`, `source_type` (`kodi`, `clz`), `source_id`, `parent_id`, `year`, `recent_days` (1–3650).

Les filtres sont combinés avec **ET**. `q` recherche tous les mots saisis par préfixe, en ignorant la casse et les accents ; ce n'est pas une recherche par sous-chaîne arbitraire ni une correction orthographique. Les facettes sont globales au catalogue.

Tri : `sort=title|date_added|year|rating|last_played`, `order=asc|desc`. Défaut : `date_added`, `desc`. Les valeurs inconnues sont toujours à la fin ; l'identifiant départage les égalités. `limit` vaut 60 par défaut, maximum 200 ; `offset` vaut 0. Résultat : `{items, total, limit, offset, has_more}`. Un nouvel import entre deux requêtes peut déplacer les résultats paginés.

Les liens d'image renvoyés commencent par `/api/v1/…` et sont relatifs à la racine du service. Un client utilisant un préfixe de proxy doit les résoudre **sous sa base du service**, comme le fait le client Swift, sans effacer le préfixe Ingress.

## Exemples indépendants du front

```bash
export KODI_LIBRARY_URL='https://catalogue.example.net'
# Définir KODI_LIBRARY_TOKEN dans votre environnement sans le publier.
curl -H "Authorization: Bearer $KODI_LIBRARY_TOKEN" \
  --get "$KODI_LIBRARY_URL/api/v1/media" \
  --data-urlencode 'q=blade runner' \
  --data-urlencode 'watch_status=unwatched' \
  --data-urlencode 'genre=Science Fiction' \
  --data-urlencode 'sort=date_added' --data-urlencode 'order=desc'
```

Créer une collection CLZ, puis utiliser son `id` dans l'import :

```bash
curl -H "Authorization: Bearer $KODI_LIBRARY_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"Mes Blu-ray","type":"clz"}' \
  "$KODI_LIBRARY_URL/api/v1/sources"
curl -H "Authorization: Bearer $KODI_LIBRARY_TOKEN" \
  -F 'source_id=ID_RETOURNE' -F 'file=@export-clz.xml' \
  "$KODI_LIBRARY_URL/api/v1/imports/clz"
```

Suivre `/jobs/ID_TACHE` avant de rafraîchir le catalogue. Chaque import applique un instantané atomique : les éditions disparues de l'export sont retirées uniquement de cette source. Un échec conserve l'ancien catalogue. Une seule tâche active par source ; import maximum 20 MiB.

Une source Kodi est créée avec `{"name":"NAS","type":"kodi","config":{"host":"192.168.1.10","port":3306,"username":"kodi_library","password":"…","database":"MyVideos131","tls":false}}`. `database` peut être vide pour la détection automatique. Un mot de passe absent ou `null` lors d'un PUT conserve le précédent ; une chaîne vide le supprime. Les réponses remplacent le mot de passe par `has_password`.

Créer une cible Kodi avec `{"name":"Salon","source_id":"ID_SOURCE_KODI","host":"192.168.1.20","port":8080,"username":"kodi","password":"…","tls":false}`, puis :

```bash
curl -H "Authorization: Bearer $KODI_LIBRARY_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"media_id":"ID_MEDIA","resume":true}' \
  "$KODI_LIBRARY_URL/api/v1/players/ID_CIBLE/play"
```

`edition_id` sélectionne un fichier/version Kodi précis. Le serveur vérifie que la cible est associée à la bonne source et refuse les identifiants distants contradictoires avant `Player.Open`. Une série nécessite de choisir un épisode via `GET /media?kind=episode&parent_id=ID_SERIE`. Le serveur ne réveille pas Kodi.

## Erreurs et évolution

Les erreurs applicatives ont la forme `{"error":{"code":"invalid_request","message":"…","details":[…]}}` ; `details` est facultatif et n'inclut pas les données soumises. Statuts principaux : 401, 403, 404, 409, 413, 422, 429, 502. Les erreurs inattendues sont journalisées côté serveur ; aucun secret n'est destiné à la réponse.

L'API expose des UUID de catalogue stables lors des réimports tant que les clés de source sont conservées. Une suppression/recréation d'une source peut changer ces UUID. Les évolutions compatibles ajoutent des champs/routes à v1 ; une rupture de contrat devra utiliser v2. Consulter le schéma OpenAPI pour les champs, types et contraintes exacts.
