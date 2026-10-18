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
| GET | `/jobs` | 30 dernières tâches, y compris celles démarrées automatiquement ; suivi d'activité web/iOS |
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

## Écriture des notes et états de visionnage

`PATCH /media/{id}/personal` accepte les modifications partielles `{"user_rating":7.5}` et `{"watched":true}`, ou `{"watched":false}`. La valeur `null` efface uniquement la préférence locale, sans modifier les données source. Les changements sont enregistrés dans SQLite et diffusés aux connecteurs disposant d'une capacité d'écriture.

Pour Kodi, l'application utilise les instances HTTP/JSON-RPC déjà associées à chaque source MariaDB. Elle vérifie la permission `UpdateData`, l'identité du média et son identifiant Kodi avant d'appeler `VideoLibrary.SetMovieDetails`, `SetEpisodeDetails` ou `SetTVShowDetails`. Une série marquée vue/non vue est propagée à ses épisodes par Kodi. Les différentes versions d'un film partagent un même `movieid` et ne provoquent donc qu'une mise à jour. Les notes Kodi sont entières : 7,5 est transmis sous la forme 8, tandis que la note locale reste 7,5.

La réponse `MediaDetail` contient `writeback: [{source_id,source_name,source_type,status,message}]`. `status` vaut `updated`, `unavailable` (aucune instance ou source désactivée) ou `failed` (connexion, permissions ou identité divergente). Ce retour ne concerne que les sources qui autorisent l'écriture ; les imports XML CLZ/MyMovies/Filmotech ne sont jamais modifiés. Les sources My Movies dotées d'un serveur Remote Data API sous licence peuvent recevoir uniquement les changements vu/non vu (`ElementWatched` et `ClearElementWatched`), jamais les notes ; les sources CLZ et Filmotech restent strictement en lecture seule. Pour l'activer, modifier la configuration de la source My Movies : `config.remote_enabled=true`, `remote_host`, `remote_port` (défaut 51414), `remote_api_key` (clé masquée dans les réponses et conservée si non fournie sur PUT). Le service Remote Data API exige une licence/professionnel autorisé et la documentation est réservée aux intégrations prévues par l'éditeur ; aucun accès direct à sa base, aucun reverse engineering et aucune modification des exports. La commande `GetTitle` vérifie l'identifiant et le titre avant écriture. Seuls les exports contenant un ID numérique peuvent servir de base au rapprochement. API HTTP historique : réserver à un réseau local de confiance. Le changement local reste enregistré si Kodi est hors ligne, mais l'échec est signalé au client. Les tentatives échouées ne sont pas rejouées automatiquement. Un nouveau PATCH est nécessaire pour retenter une synchronisation. Les droits MariaDB restent en lecture seule.

### Repli d'écriture Kodi (MariaDB)

Kodi JSON-RPC reste prioritaire pour les modifications de notes et états vu/non vu. Si **toutes** les instances JSON-RPC associées à la source sont injoignables (réseau/timeout), un **repli SQL optionnel**, désactivé par défaut, peut mettre à jour `movie.userrating`, `tvshow.userrating`, `episode.userrating` et `files.playCount` / `files.lastPlayed` (pour une série, les fichiers de ses épisodes). Ce mode ne se déclenche **pas** si Kodi répond mais refuse la commande ou la permission `UpdateData`.

Configurer explicitement `config.database="MyVideos…"`, `config.mariadb_write_enabled=true`, `config.write_username` et `config.write_password` sur la source Kodi. Utiliser un utilisateur distinct de celui de l'import SELECT, avec `SELECT` sur les tables d'identification et `UPDATE` limité à `movie.userrating`, `tvshow.userrating`, `episode.userrating` et `files.playCount`, `files.lastPlayed`. Le repli exige une correspondance d'identifiant externe et de titre ; il s'exécute dans une transaction SQL et refuse les données divergentes. Aucun événement Kodi n'est produit par une mise à jour SQL : l'interface Kodi peut nécessiter un rafraîchissement manuel.

Chaque PATCH expose dans `writeback[]` les champs `method` et `attempts`, par exemple `["jsonrpc:unavailable","mariadb:updated"]`. Une trace persistante sans secret est consultable par `GET /media/{id}/writeback` et `GET /sources/{id}/writeback` (max. 200 entrées).

### Actualiser Kodi puis synchroniser le catalogue local

`POST /sources/{id}/scan` (option `player_id`) démarre une tâche `JobView` via un lecteur Kodi JSON-RPC. Le service se connecte d'abord au WebSocket Kodi `ws://HOST:9090/jsonrpc` (port modifiable sur le lecteur via `websocket_port`), contrôle `UpdateData`, puis envoie `VideoLibrary.Scan`. Une fois `VideoLibrary.OnScanStarted` et `VideoLibrary.OnScanFinished` observés sur cette même connexion, la tâche enchaîne automatiquement avec la lecture MariaDB et la publication atomique du catalogue SQLite. Suivre `GET /jobs/{id}` ; le résultat final contient `kodi_scan="finished"`.

En cas de perte du WebSocket ou d'absence de confirmation de fin, la tâche échoue **sans** marquer le scan comme terminé ni lancer l'import local. L'API WebSocket/TCP doit être activée dans Kodi ; le service HTTP JSON-RPC seul ne fournit pas les notifications de fin d'analyse. Une seule tâche de synchronisation ou de scan est acceptée par source simultanément.


## Diagnostic des suppressions Kodi

Une synchronisation interroge **movie** et **movie_view** : la table `movie`
constitue la source d'autorité pour les identifiants de films. Même si une
ligne obsolète subsiste dans `movie_view`, elle ne sera pas importée si
son `idMovie` est absent de `movie`. Le rapport de tâche contient
`movie_table_count`, `orphaned_movie_view_rows` et un échantillon
`orphaned_movie_view_examples` (limité à 10 lignes).

La fiche film expose, en mode administrateur, le bouton
**Vérifier la présence dans la vidéothèque** :
`GET /api/v1/media/{id}/diagnostics` interroge uniquement la base
MariaDB MyVideos et vérifie si la ligne `movie_view` possède une version
par défaut (`isDefaultVersion = 1`). Il distingue un film encore présent
dans `movie` d'un film absent du parcours Kodi. Les liens
`movie`/`files`/`path`/`videoversion` restent disponibles en diagnostic
secondaire, sans aucun contrôle du stockage ni appel HTTP à Kodi.

Ce critère reproduit `CVideoDatabase::GetFilter` puis
`GetMoviesByWhere` pour `videodb://movies/titles/`, en affichage
standard (versions non aplaties). Le mode *versions aplaties* de Kodi
emploie un autre prédicat (`videoVersionTypeItemType`) dépendant d'un
réglage local qui n'est pas stocké dans MariaDB. Les éditions non par
défaut d'un film listé sont conservées, mais les films sans version
par défaut valide sont exclus, même si une ligne existe encore dans
`movie`. Les extras ne sont pas importés. Pour les schémas anciens
sans `isDefaultVersion`, le connecteur retombe sur le comportement
historique et le rapport indique `library_verified=false` ; aucune
suppression n'est déduite artificiellement d'un fichier manquant.

**Aucune instance Kodi n'est nécessaire**, y compris lorsqu'un lecteur
est configuré pour la lecture. Le résultat de synchronisation expose
`library_verified`, `library_rule`, `library_movie_count`,
`library_excluded_editions` et `library_excluded_examples`.

Une synchronisation récupère un instantané **complet** des entrées de la base
`MyVideos` effectivement sélectionnée. Les éditions absentes sont supprimées
de SQLite dans la même transaction, puis les fiches sans aucune édition sont
retirées (y compris des listes et des notes). Une œuvre qui possède encore une
édition CLZ **reste** dans le catalogue mais perd ses éditions et métadonnées Kodi.

Le résultat `GET /jobs/{id}` contient `database` (nom MyVideos interrogé),
`source_records_before`, `source_records_after`, `removed_editions`,
`removed_works`, `kept_clz_works`, `removed_titles` et
`removed_titles_total`. Le serveur vérifie que les clés Kodi persistées
correspondent exactement aux clés reçues ; une incohérence annule la transaction.

Si `removed_editions=0` malgré une suppression dans Kodi, vérifiez d'abord
la base `database` du résultat de la tâche : plusieurs bases `MyVideos…`
peuvent cohabiter sur MariaDB ; une source configurée sans nom explicite
utilise la version numérique la plus élevée **accessible** (pas nécessairement
celle de l'instance Kodi consultée). Dans **Paramètres → source Kodi → Modifier**,
renseignez alors explicitement la bonne base, puis synchronisez. Vérifiez aussi
les autres sources Kodi et éditions CLZ de la fiche. Les fichiers simplement
supprimés du NAS sans nettoyage de la médiathèque Kodi restent référencés dans
la base et ne peuvent pas être distingués par ce connecteur en lecture seule.

Les réponses `GET /jobs` et `GET /jobs/{id}` exposent
`progress_phase`, `progress_current` et `progress_total` (lignes de
l'étape courante), ainsi que `overall_percent` (estimation monotone de 0 à
100 % pour toute la tâche, sans retour à zéro entre films, séries et épisodes).
La lecture MariaDB et la publication SQLite ayant des durées variables, ce
pourcentage **global est une estimation**, pas une mesure du temps restant.
Les étapes sans total connu affichent une animation indéterminée et conservent
une progression globale estimée cohérente.

Une nouvelle synchronisation ignore désormais les éditions dont la charge
utile est identique à la précédente. Les rapports retournent
`unchanged_editions`, `rebuilt_works` et, pour le diagnostic de lenteur,
`mariadb_read_ms`, `sql_view_fetch_ms` et `sqlite_publish_ms`.
Le serveur limite les mises à jour SQLite du compteur d'avancement à deux
fois par seconde (plus les changements d'étape). Les extractions de casting
évitent les tris globaux coûteux dans MariaDB et préservent l'ordre des acteurs
par œuvre côté application.

`GET /jobs` sert au voyant d'activité : les tâches planifiées et manuelles
sont visibles au rechargement et leur état reste consultable même si le navigateur
ou l'application iOS a été fermé pendant le traitement.

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
