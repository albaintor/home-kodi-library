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
| POST | `/connections/mariadb/test` | Test en lecture seule d'une configuration MariaDB sans enregistrement |
| POST | `/connections/kodi/test` | Test HTTP/JSON-RPC, version Kodi et autorisation de lecture sans enregistrement |
| POST | `/connections/tmdb/test` | Test de la clé TMDB configurée sur le serveur, sans corps ni secret côté client |
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

Les noms et rôles du casting Kodi participent à la recherche. La fiche `/media/{id}` expose `cast: [{name, role, order}]`. `formats` contient les formats physiques et les labels techniques connus (par exemple `4K`, `1080p`, `3D`, `Dolby Vision`, `HDR10+`, `Dolby Atmos`). Les métadonnées de chaque édition contiennent `cast`, `video_streams`, `audio_streams` et les sous-titres lorsque Kodi les fournit. Ces flux sont propres au fichier, y compris aux versions alternatives ; les formats de l'œuvre réunissent ceux de ses éditions.

Le défilement automatique de l'interface utilise ces mêmes routes paginées : première requête avec `offset=0&limit=60`, puis offsets 60, 120, etc., en conservant les filtres et le tri. Un client mobile peut utiliser la même pagination et `has_more`.

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

Avant de créer une source, `POST /connections/mariadb/test` accepte `{"config":{"host":"192.168.1.10","port":3306,"username":"kodi_library","password":"…","database":"","tls":false}}`. Le test vérifie l'accès aux vues et tables utilisées sans importer le catalogue. La réponse contient `success`, `kind`, `message`, `database` et `available_media`.

Pour une cible, `POST /connections/kodi/test` accepte `{"host":"192.168.1.20","port":8080,"username":"kodi","password":"…","tls":false}`. Il appelle `JSONRPC.Ping`, `JSONRPC.Permission` et `Application.GetProperties`, sans lancer de média. La réponse contient notamment `version` et `playback_allowed`. Kodi doit être démarré et autoriser le contrôle HTTP. Ce test ne garantit pas que l'instance utilise la même base MyVideos ; cette association reste à configurer.

Ces routes requièrent le jeton administrateur et ne créent ni source, ni cible, ni tâche. Les échecs de connexion renvoient HTTP 502 avec une cause réseau, authentification ou permissions, sans mot de passe. Pour tester une modification, ajouter `source_id` ou `player_id` : un mot de passe absent ou `null` réutilise celui enregistré ; une chaîne vide teste sans mot de passe. L'interface teste automatiquement avant tout nouvel ajout ; les routes de création restent utilisables directement pour préparer une configuration hors ligne.

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

## Données personnelles et listes

Toutes ces écritures exigent les droits administrateur ; Bearer externe, session avec CSRF, accès local avec CSRF ou secret Ingress. Les lectures sont aussi accessibles avec le jeton de lecture.

| Méthode | Route `/api/v1` | Corps / résultat |
|---|---|---|
| PATCH | `/media/{id}/personal` | `{ "user_rating": 8.5, "watched": true }` → fiche mise à jour |
| GET | `/lists` | Listes, descriptions, compteurs, révisions |
| POST | `/lists` | `{ "name": "Films à voir", "description": "Priorités" }` → liste créée (201) |
| PUT | `/lists/{id}` | Nom et description |
| DELETE | `/lists/{id}` | Supprime uniquement la liste (204) |
| GET | `/lists/{id}/items` | Page `items,total,limit,offset,has_more,revision` |
| POST | `/lists/{id}/items` | `{ "media_id": "UUID" }` ; ajout idempotent en fin de liste (204) |
| DELETE | `/lists/{id}/items/{media_id}` | Retire le média de la liste (204) |
| PUT | `/lists/{id}/order` | `{ "media_ids": ["UUID2","UUID1"], "revision": 3 }` ; permutation complète (204) |

`PATCH personal` laisse inchangés les champs omis ; `null` retire l'édition personnelle du champ et reprend sa valeur importée. `user_rating` accepte 0 à 10. `watched` accepte true/false/null. Les valeurs effectives apparaissent dans les pages, fiches et filtres ; le tri `user_rating` est disponible dans le catalogue. Les éditions conservent leurs métadonnées d'origine. Ces valeurs sont communes à l'installation, sans profils utilisateur.

Les items de liste acceptent `sort=position|title|year|date_added|rating|user_rating`, `order=asc|desc`, `limit=1..200` et `offset>=0`. Un tri automatique ne réécrit pas `position`. Pour réordonner une liste paginée, lire tous les UUID dans une même révision puis envoyer chacun exactement une fois (maximum 10 000). Un changement concurrent donne 409 ; une permutation incomplète ou dupliquée donne 422. Les mutations, y compris la disparition d'un média de toutes les sources, changent la révision. La fiche comporte `personal` et `lists`.

## Personnes et cache TMDB

`MediaDetail.people` regroupe `name,role,order,category,imdb_id,tmdb_id,links,thumbnail`. `category` vaut `cast`, `director`, `writer`, `producer`, `composer`, `photography` ou `crew`. `cast` reste disponible pour les anciens clients. `links` contient les URL directes avec identifiants ou une `imdb_search` explicitement distincte. Les portraits sont exposés par une route relative authentifiée `GET /media/{id}/people/{index}/artwork` ; conserver le préfixe d'un éventuel Ingress.

`trailers` contient des liens HTTPS YouTube validés (les liens plugin Kodi sont convertis). `tmdb_available` indique si le serveur dispose de son jeton TMDB. `enriched_at` donne la date du cache persistant. `POST /media/{id}/enrich` récupère les personnes/portraits/bandes-annonces de TMDB pour un film ou une série identifiés par IMDb/TMDB ; réutilise le cache pendant 30 jours. `POST /media/{id}/enrich?refresh=true` force le renouvellement et invalide les portraits concernés. Une erreur conserve le cache précédent. Aucune clé TMDB n'est fournie par le client ou révélée dans la réponse. Les épisodes ne sont pas encore enrichis individuellement.

`GET /status` expose également `tmdb_available`. `POST /connections/tmdb/test` exige les droits d'écriture ; `422` signifie que la clé n'est pas configurée, `502` une erreur TMDB/réseau, `200` un accès vérifié. `TMDB_API_TOKEN` accepte le jeton de lecture Bearer ou la clé v3. Avec un `edition_id` explicite, `/players/{id}/play` transmet à Kodi le chemin de ce fichier lorsqu'il est disponible ; la vérification des identifiants de bibliothèque reste préalable.

Les `GET` ne déclenchent pas de requêtes TMDB. Le front administrateur appelle automatiquement `POST enrich` à l'ouverture si nécessaire ; une application iOS peut utiliser les mêmes routes. Les clients de lecture consultent les informations importées ou déjà enrichies. Le champ `editions[].metadata.file` expose le chemin Kodi ; le lancement d'une version utilise `edition_id` dans `/players/{id}/play`.

## Plages locales sans jeton d'accès

Configurer `LOCAL_AUTH_NETWORKS` (JSON de CIDR) et, si nécessaire, `TRUSTED_PROXY_NETWORKS` dans l'environnement. Aucune plage n'est autorisée par défaut. `GET /auth/session` renvoie `access_method=local` et un CSRF pour les clients autorisés sans Bearer ; envoyer ce CSRF dans `X-CSRF-Token` pour les écritures. Le front le fait automatiquement. Le contrôle Bearer reste disponible indépendamment de l'interface et prioritaire sur l'autorisation locale. Consulter le README pour la configuration du proxy ; ne pas activer le traitement automatique des proxy headers dans Uvicorn.
