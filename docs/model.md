# Modèle et persistance

## Catalogue et connecteurs

- **Source** : base Kodi MariaDB ou collection XML CLZ, paramètres, dernier import et erreur éventuelle.
- **Œuvre** : film, série ou épisode, identifié par UUID ; métadonnées consolidées pour recherche et affichage.
- **Édition/enregistrement** : une entrée stable d'une source. Plusieurs disques CLZ ou fichiers/versions Kodi peuvent appartenir à la même œuvre.
- **Identifiants** : IMDb/TMDB/TVDB normalisés, associés au type de média. Les codes-barres et identifiants CLZ restent dans les métadonnées d'édition.
- **Cible de lecture** : instance Kodi HTTP, liée explicitement à une source Kodi.
- **Tâche** : import/synchronisation asynchrone, état et compteurs.

Les connecteurs produisent le même `Record` interne. Un futur connecteur transforme son export/API dans ce format et réutilise la logique d'instantané et de rapprochement. Le modèle réserve les métadonnées propres à chaque source.

Les rapprochements nécessitent un identifiant IMDb, TMDB ou TVDB commun, un type identique et l'absence de contradiction avec les identifiants connus. Un titre similaire ne fusionne jamais un remake avec son original. Les cas ambigus restent séparés et sont comptés dans `matching_conflicts`. Il n'existe pas encore d'écran de résolution manuelle.

Les métadonnées Kodi ont priorité, avec CLZ comme complément si le champ manque. Les formats, genres et collections sont réunis. La date d'ajout privilégie Kodi puis CLZ. La recherche SQLite FTS5 est accent-insensible et consulte le catalogue local même si MariaDB ou Kodi sont arrêtés.

## État de visionnage

L'API distingue `watched`, `unwatched`, `in_progress` et `unknown`.

- Kodi : compteur de lecture et point de reprise ; pour les séries, comptes d'épisodes vus/total.
- CLZ : `<seenhistory><seendate><date><year>…</year><month>…</month><day>…</day></date></seendate></seenhistory>`. Au moins un `seendate` signifie vu ; chaque entrée compte un visionnage. Les dates valides sont conservées dans `metadata.seen_dates`, et la plus récente dans `last_played`.
- Un `seenhistory` explicitement vide signifie non vu. Une balise absente signifie inconnu, car l'export peut ne pas inclure ce champ. Une date mal formée n'efface pas le fait qu'un visionnage a été enregistré.

Une œuvre est **vue si au moins une édition/source la signale vue**, même si le fichier Kodi est non lu. Sinon elle est en cours si Kodi indique une reprise ou une série partiellement vue ; sinon non vue si une source fournit explicitement cet état ; sinon inconnue. Les états par édition restent disponibles pour distinguer « disque vu » et « fichier Kodi non lu ». Aucun état n'est écrit dans Kodi ou CLZ.

Les nombres de lectures sont le maximum connu entre sources/éditions, pas leur somme : un même visionnage peut être enregistré plusieurs fois. La dernière date est la plus récente connue. Les dates CLZ sans heure sont `YYYY-MM-DD` ; les horodatages Kodi conservent leur heure sans inventer un fuseau, et les timestamps CLZ sont convertis en UTC.

## SQLite et sauvegardes

Le volume `/data` contient `catalogue.db` (SQLite WAL) et le cache `artwork/`. Un seul processus serveur, accès indexés, FTS5, transactions atomiques, délai d'attente des verrous. Le numéro de schéma est versionné avec `PRAGMA user_version` (v1) ; un schéma plus récent provoque un refus de démarrage.

Les mots de passe des sources/cibles sont stockés dans la base locale, en clair au repos, protégés par les permissions du volume et expurgés des réponses. Le dossier est privé et le fichier SQLite utilise le mode 0600. Protéger et sauvegarder ce volume comme un fichier de configuration contenant des secrets.

Une sauvegarde fiable consiste à arrêter le conteneur puis copier **tout le dossier `data`**, ou à utiliser l'API de sauvegarde SQLite. Ne pas copier seulement `catalogue.db` pendant une écriture WAL. Conserver aussi `.env` dans un emplacement protégé. Les tâches interrompues sont marquées échouées au prochain démarrage ; le dernier instantané validé reste disponible.

Le cache d'affiches conserve les fichiers 30 jours et évince les plus anciens au-delà de 512 MiB lors des écritures. Un fichier image est limité à 8 MiB ; seules des images raster reconnues sont acceptées. Les URL d'image privées, redirections et URL avec identifiants sont refusées afin que les métadonnées importées ne permettent pas de requêtes arbitraires vers le réseau local.

## Affiches stockées sur le NAS

Si Kodi ou CLZ contient `smb://nas/media/posters/film.jpg`, monter le répertoire correspondant :

```yaml
# compose.yaml, sous volumes
- /volume1/media/posters:/artwork:ro
```

```dotenv
# .env : objet JSON, préfixe terminé par un slash
ARTWORK_MOUNTS='{"smb://nas/media/posters/":"/artwork/"}'
```

Un préfixe local tel que `/home/user/covers/` ou `file:///covers/` peut être mappé de la même façon. Le suffixe est résolu sous le montage ; les traversées `../` et liens symboliques vers l'extérieur sont refusés. Les URL `image://` de Kodi sont décodées par le connecteur. Une image HTTP privée exige également un mapping explicite vers un fichier monté, ou une URL publique accessible.

## Limites de cette version

Le connecteur s'appuie sur `movie_view`, `tvshow_view`, `episode_view`, `art` et `uniqueid`, avec colonnes facultatives selon la version. Les requêtes sont en lecture seule et l'absence de vues optionnelles n'empêche pas les films. Une validation sur la version réelle MyVideos est nécessaire avant de déclarer la compatibilité de toutes les métadonnées, en particulier celles des versions alternatives.

Les schémas avec versions de films n'utilisent pas tous le même type numérique : `VERSION=0` dans [Kodi 21](https://github.com/xbmc/xbmc/blob/21.0-Omega/xbmc/video/VideoManagerTypes.h), `VERSION=1` dans le [code actuel de Kodi](https://github.com/xbmc/xbmc/blob/master/xbmc/video/VideoManagerTypes.h). Le connecteur détecte ce type à partir des fichiers de film par défaut, conserve les versions alternatives et exclut les bonus. Les anciens schémas sans cette colonne restent pris en charge.

`actor` et `actor_link` fournissent les interprètes, rôles et ordre du casting. `streamdetails` fournit définition vidéo, codecs, type HDR, stéréoscopie, pistes audio et langues par fichier. Les champs HDR facultatifs restent inconnus lorsqu'ils sont absents. Le codec TrueHD seul ne prouve pas Atmos ; CLZ peut compléter l'œuvre avec ses pistes explicitement intitulées Dolby Atmos. L'export CLZ initial fourni ne contient pas de casting renseigné. Aucune base supplémentaire ni migration SQLite n'est nécessaire pour ces métadonnées, enregistrées dans les éditions.

Les séries CLZ sont des éditions de collection ; leurs listes d'épisodes éventuelles ne sont pas importées comme épisodes Kodi. La lecture concerne les fichiers Kodi, pas la lecture automatique d'un disque physique. Ni réveil de machine, ni modification du statut vu, ni utilisateurs multiples ne sont encore implémentés.
