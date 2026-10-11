# Home Kodi Library

Catalogue indépendant pour consulter une bibliothèque Kodi et une collection physique CLZ Movies. Un conteneur Docker expose une interface web responsive et une **API REST versionnée**, utilisable directement par une future application iOS. Home Assistant peut servir de point d'entrée via Ingress.

## Première version

- Recherche libre dans les titres, synopsis, genres, collections et certaines métadonnées ; recherche sans distinction d'accents, avec correspondance par préfixe de mots.
- Filtres combinables : film/série/épisode, Kodi/CLZ, genre, formats vidéo/audio/support cumulables (ET), collection, vu/non vu/en cours/inconnu, ajouts des 30 derniers jours. L'API ajoute année, source précise et série parente. Les formats 4K et 4K UHD sont unifiés sous 4K ; filtres et tris sont mémorisés dans le navigateur.
- Tri par titre, date d'ajout, année, note ou dernier visionnage ; ordre croissant/décroissant. Chargement progressif par lots de 60 au défilement, avec bouton de secours et reprise après erreur.
- Vue pochettes ou liste, fiche détaillée, notes, synopsis, éditions physiques et fichiers Kodi, reprise et historique de lecture.
- Fiche responsive avec casting Kodi (interprètes et rôles), badges de définition vidéo, 3D, Dolby Vision/HDR10/HDR10+, audio Dolby/DTS et formats physiques. Les caractéristiques restent détaillées par édition dans l'API.
- Synchronisation **en lecture seule** de MariaDB Kodi, films/séries/épisodes, identifiants IMDb/TMDB/TVDB, versions alternatives si exposées par `movie_view`.
- Import XML CLZ Movies : éditions, formats Blu-ray/4K/3D/DVD, codes-barres, coffrets, langues, sous-titres et historique `seenhistory`.
- Rapprochement Kodi/CLZ par identifiant fiable et type de média ; conservation des éditions. Les titres seuls ne provoquent pas de fusion.
- Plusieurs cibles Kodi HTTP/JSON-RPC associées à une source MariaDB. Lecture ou reprise sur une instance **déjà démarrée**, sans obligation d'utiliser les entités Home Assistant.
- Tests de connexion MariaDB et Kodi avant ajout : réseau, authentification, base MyVideos, droits de lecture et contrôle HTTP de Kodi. Disponibles également dans l'API.
- SQLite locale persistante et cache des images : **aucune seconde MariaDB**.
- Jeton administrateur, jeton de consultation facultatif, sessions navigateur protégées contre CSRF et secret Ingress séparé.

L'interface utilise exactement la même API que les autres clients. Elle ne contient pas de logique métier indispensable au fonctionnement du serveur.

## Démarrage Docker sur un NAS

```bash
git clone https://github.com/albaintor/home-kodi-library.git
cd home-kodi-library
cp .env.example .env
openssl rand -hex 32
```

Copier le jeton généré dans `API_TOKEN` de `.env`. Générer un **deuxième** jeton pour `INGRESS_TOKEN` si Home Assistant doit donner accès au catalogue. `API_READ_TOKEN`, également distinct, est facultatif.

```bash
mkdir -p data
sudo chown 1000:1000 data
chmod 700 data
chmod 600 .env
docker compose up -d --build
```

Adapter `PUID`/`PGID` et le propriétaire du dossier aux comptes du NAS si nécessaire. Ouvrir `http://IP_DU_NAS:8099`, puis saisir `API_TOKEN`. Dans **Paramètres**, ajouter la source Kodi et/ou importer un export XML CLZ. Les mises à jour Kodi sont ensuite automatiques, toutes les 300 secondes par défaut. Un nouvel export CLZ remplace uniquement les entrées de la collection CLZ choisie.

Pour MariaDB, utiliser un compte avec `SELECT` sur la base `MyVideos…`. Renseigner explicitement la base en cas de plusieurs bibliothèques. Sans nom, le connecteur sélectionne la version numérique accessible la plus élevée ; toutes les vues doivent être accessibles avec ce compte. Les connexions et mots de passe sont enregistrés dans le volume local, jamais renvoyés par l'API.

Après une mise à jour ajoutant des métadonnées, utiliser **Paramètres → Synchroniser** pour Kodi et réimporter l'XML dans la **collection CLZ existante**. Les badges HDR et Atmos utilisent uniquement les indications explicites de la source ; un disque UHD ou une piste TrueHD ne suffit pas à annoncer ces technologies.

### Mises à jour suivantes

Le script `rebuild.sh` récupère `main` par `git pull --ff-only`, construit l'image avec le réseau de l'hôte (installation Python), puis recrée `library` en conservant `compose.dns.yaml` et les paramètres DNS de `.env`. Il attend jusqu'à 120 secondes la sonde de santé du conteneur et affiche les journaux si le démarrage échoue.

```bash
# Une fois, pour récupérer le script :
git pull --ff-only
# Puis, pour chaque mise à jour :
sh ./rebuild.sh
```

Lancer le script avec l'utilisateur propriétaire du dépôt ; il utilise automatiquement `sudo` pour Docker si nécessaire. Ne pas lancer tout le script avec `sudo`, afin de conserver les permissions Git du dépôt. `.env` et `data/` sont conservés. Un conflit Git ou une erreur de construction arrête le script avant de recréer le conteneur ; aucune commande `down`, réinitialisation Git ou suppression de données/images n'est exécutée. Le script utilise les fichiers Compose indiqués ci-dessus ; ajouter vos éventuels fichiers supplémentaires dans `compose_run` si votre installation en nécessite.

Les posters HTTP(S) publics sont téléchargés et mis en cache. Les images sur SMB/NFS ou un chemin local doivent être montées en lecture seule et déclarées dans `ARTWORK_MOUNTS` ; voir [le modèle et les images](docs/model.md). Les affiches indisponibles laissent une vignette lisible.


## Si la construction échoue sur une erreur DNS

Un message `Temporary failure in name resolution` pendant `pip install` signifie que l'environnement de construction n'arrive pas à résoudre le serveur de paquets. Le message final `No matching distribution found` ne permet alors pas de conclure que la version manque.

Sur un NAS Linux/Synology, essayer de construire avec le réseau du NAS :

```bash
sudo docker build --network=host -t home-kodi-library:local .
sudo docker compose up -d --no-build
```

La première commande utilise le réseau hôte pendant la construction de l'image. La deuxième démarre l'image déjà construite avec le réseau du service défini dans `compose.yaml`.

Une variante Compose est fournie pour conserver cette option dans les futures reconstructions :

```bash
git pull --ff-only
sudo docker compose -f compose.yaml -f compose.build-host.yaml up -d --build
```

L'option `build.network` concerne les instructions `RUN` du Dockerfile. Ajouter `dns:` au service concerne les conteneurs en fonctionnement et ne corrige pas à lui seul le DNS de construction. Voir la [documentation Docker](https://docs.docker.com/reference/compose-file/build/#network).

Si le réseau hôte échoue également, vérifier la résolution depuis le NAS puis depuis un conteneur :

```bash
nslookup pypi.org
sudo docker run --rm python:3.12-slim python -c 'import socket; print(socket.getaddrinfo("pypi.org", 443))'
sudo docker run --rm --dns 1.1.1.1 --dns 8.8.8.8 python:3.12-slim python -c 'import socket; print(socket.getaddrinfo("pypi.org", 443))'
```

Le dernier test utilise des DNS publics pour identifier un éventuel problème de DNS Docker. S'il réussit seul, les paramètres DNS du NAS/Docker doivent être corrigés ; le DNS configuré au niveau du service pourra aussi être nécessaire pour télécharger les affiches en fonctionnement.


## Si les pochettes distantes ne s'affichent pas

Les URL de pochettes CLZ sont importées depuis l'export XML et téléchargées par le conteneur. Une correction DNS de **construction** (`build.network: host` ou `docker build --network=host`) ne configure pas les DNS du conteneur en fonctionnement.

Si la résolution DNS Docker échoue, une variante optionnelle permet de choisir des DNS explicites :

```bash
git pull --ff-only
sudo docker compose -f compose.yaml -f compose.dns.yaml up -d --no-build --force-recreate library
```

Elle utilise `1.1.1.1` et `8.8.8.8` par défaut. Pour utiliser votre routeur/résolveur et conserver la résolution des noms de machines locaux, renseigner `DNS_PRIMARY` et `DNS_SECONDARY` dans `.env`. Conserver `-f compose.dns.yaml` lors des prochains `up`/recréations pour conserver cette configuration. On peut aussi combiner ce fichier avec `compose.build-host.yaml` lors des reconstructions.

Tester une URL réelle de l'export CLZ depuis le conteneur :

```bash
sudo docker compose exec library python -c 'import urllib.request; u="https://clzmovies.r.sizr.io/core/covers/lg/f4/f4_1259989_0_DungeonsDragonsHonorAmongThiev.jpg"; r=urllib.request.urlopen(u, timeout=15); print(r.status, r.headers.get("Content-Type"), r.geturl()); r.close()'
```

Un résultat `200 image/jpeg` (ou un autre type d'image) indique que le téléchargement direct fonctionne. Une erreur de résolution, de certificat ou un code HTTP apporte la cause du blocage. Cette commande sert au diagnostic réseau ; le proxy d'images de l'application applique en plus ses contrôles sur les URL.

Pour les pochettes Kodi utilisant un chemin SMB/NFS/local, utiliser le mapping `ARTWORK_MOUNTS` décrit dans [le modèle](docs/model.md), car une configuration DNS seule ne donne pas accès aux fichiers.

## Depuis Home Assistant et depuis iOS

- [Accès Home Assistant avec hass_ingress](docs/home-assistant.md), y compris à distance depuis la même URL Home Assistant.
- [Contrat API, authentification et exemples curl](docs/api.md).
- [Schéma OpenAPI v1](docs/openapi-v1.json), généré depuis le serveur ; Swagger interactif à `/docs` après connexion web.
- [Exemple de client Swift avec URLSession](examples/KodiLibraryClient.swift).
- [Modèle, règle vu/non vu, rapprochements et sauvegarde](docs/model.md).
- [Résultats et limites des vérifications initiales](docs/validation.md).

Pour accéder directement au Docker hors du réseau local, utiliser un VPN ou une URL HTTPS via un reverse proxy. L'accès distant à Home Assistant ne publie pas automatiquement l'API pour un client iOS. Ce client utilise sa propre URL et un jeton Bearer ; le secret Ingress reste réservé au proxy Home Assistant.

## Développement et vérifications

Python 3.12 minimum, un processus serveur. Les sessions et tâches d'import sont locales au processus ; ne pas ajouter plusieurs workers.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
export DATA_DIR="$PWD/data"
export API_TOKEN="$(openssl rand -hex 32)"
uvicorn kodi_library.app:create_app --factory --port 8099 --no-proxy-headers
```

```bash
pytest -q
ruff check kodi_library tests
ruff format --check kodi_library tests
```

Vérification navigateur avec un catalogue synthétique, sans données personnelles :

```bash
pip install -e '.[dev,browser]'
playwright install chromium
PYTHONPATH=. python tests/browser/check_catalogue.py
```

Les tests utilisent un XML synthétique et des connexions simulées ; aucun export privé, mot de passe ni base Kodi ne figure dans le dépôt. La première validation réelle de MariaDB, de Docker sur le NAS et de hass_ingress reste à réaliser sur l'installation cible. Ce dépôt fournit un service Docker autonome, pas encore une intégration HA installable ni un add-on Supervisor.

## Personnes, portraits et bandes-annonces

La fiche distingue casting, réalisation, scénario, production, composition, photographie et autres fonctions. Kodi fournit les rôles, réalisateurs/scénaristes et portraits présents dans sa base. Les crédits/portraits exportés par CLZ et ses liens YouTube sont importés lorsqu'ils existent. L'export initial fourni contient des sections de casting vides : les images visibles sur CLZ Cloud ne sont pas incluses dans cet XML.

Pour compléter automatiquement les films et séries depuis TMDB, renseigner dans `.env` le **API Read Access Token** de [votre compte TMDB](https://www.themoviedb.org/settings/api), puis exécuter `sh ./rebuild.sh`. La **clé API v3** est également acceptée dans ce champ. Le mot de passe du compte ne convient pas.

```dotenv
TMDB_API_TOKEN=votre_jeton_de_lecture_TMDB
```

Dans **Paramètres → Métadonnées et portraits · TMDB**, l'interface indique si TMDB est configuré et propose **Tester la connexion TMDB**. Les erreurs de clé refusée, de réseau ou de limite de requêtes sont explicites. L'absence de TMDB est aussi indiquée dans chaque fiche. Activer TMDB ne nécessite aucune réimportation du catalogue : rouvrir la fiche suffit lorsque son identifiant IMDb/TMDB existe déjà.

L'enrichissement est déclenché à l'ouverture d'une fiche par un administrateur, à partir de l'identifiant IMDb/TMDB du média. Il récupère acteurs, fonctions, portraits et bandes-annonces YouTube, sans rapprochement approximatif par titre. Les noms **et portraits** ouvrent une fiche TMDB/IMDb ; sinon un lien explicitement nommé « Recherche IMDb » est disponible. Le bouton YouTube est placé en haut de la fiche : il ouvre directement une bande-annonce unique, propose un choix si plusieurs sont disponibles et lance une recherche YouTube si aucune vidéo n'a été fournie.

Les métadonnées restent dans SQLite pendant les redémarrages et synchronisations. Elles sont réutilisées pendant 30 jours, puis renouvelées à la prochaine ouverture. Le bouton **Rafraîchir les métadonnées TMDB** force leur renouvellement et invalide les images concernées dans le cache ; une panne conserve les informations précédentes. Les portraits utilisent le même proxy authentifié, cache 30 jours/512 MiB et règles de sécurité que les pochettes. Les clients API peuvent déclencher l'enrichissement sans utiliser le front. TMDB reste facultatif : la consultation des données importées/mises en cache fonctionne sans réseau ni jeton TMDB.

Le navigateur mémorise la recherche, les filtres, le tri et son sens, le mode d'affichage et le masquage des filtres. Les facettes retirées du catalogue sont ignorées au rechargement. Le défilement du catalogue est bloqué pendant l'ouverture d'une fiche ou d'une liste et retrouve sa position à la fermeture ; le défilement reste indépendant dans les dialogues imbriqués.

## Notes, visionnage et listes

En haut de la fiche, l'étoile permet de modifier **votre note sur 10** au survol, au clic ou au clavier ; une étoile ou le curseur par demi-point enregistre immédiatement la note. La pastille **Vu / Non vu** bascule et enregistre le statut au clic, sans bouton de sauvegarde. Ces valeurs personnelles sont partagées par les utilisateurs de cette installation et conservées dans SQLite ; elles prennent priorité sur les imports. Une note vide ou « Depuis les sources » rétablit la valeur importée. La note publique IMDb/Kodi, les compteurs de lecture et les fichiers sources restent des informations importées : les éditions personnelles ne sont pas réécrites dans MariaDB ou CLZ. Aucun nouveau serveur de base de données n'est requis.

Dans le catalogue, la zone **Listes personnalisées** propose **＋ Créer une liste** et **Ouvrir mes listes**. Le bouton **Mes listes** reste accessible dans l'en-tête. Créer une liste ouvre directement le formulaire de nom ; ce formulaire est aussi visible dès la première ouverture quand aucune liste n'existe. Utiliser un accès administrateur pour créer ou modifier : un accès de lecture affiche une explication dans le dialogue.

**Mes listes** permet de créer, renommer et supprimer des listes, rechercher des **films ou séries** à ajouter avec **＋ Ajouter des médias**, retirer des éléments et les déplacer avec ↑/↓ en mode **Ordre manuel, croissant**. Une fiche dispose également d'un bouton **＋ Ajouter à une liste**, avec création de liste directement depuis ce dialogue ; les noms des listes déjà associées sont cliquables. La dernière liste consultée et son tri sont mémorisés sur le navigateur. Les tris par titre, année, date d'ajout, note publique et note personnelle sont des vues de la liste et ne modifient pas l'ordre manuel enregistré. Les listes ont une pagination et un numéro de révision pour éviter qu'une autre modification soit écrasée lors d'un réordonnancement. Elles sont indépendantes des collections/coffrets importés de CLZ.

Les UUID des œuvres conservent les associations lors d'une synchronisation normale. Si un média disparaît de toutes les sources, il est retiré de ses listes ; supprimer une liste ne supprime aucun média. La mise à jour migre automatiquement SQLite du schéma 1 vers 2. Sauvegarder le volume `data` avant de revenir à une version antérieure : elle ne peut pas lire le schéma 2.

## Chemins Kodi et lecture

Les chemins complets des fichiers sont visibles dans la fiche, avec **Copier le chemin** (sur HTTP, un dialogue de copie remplace l'accès au presse-papiers). **Lire sur Kodi** contacte l'instance choisie depuis le back-end, et permet de sélectionner le fichier/version ; la sélection explicite ouvre ce chemin exact, même pour une version par défaut. Kodi utilise ses propres accès au NAS.

Sans instance associée à la source, la fiche propose **Configurer une instance Kodi**. Le bouton avec le logo Kodi ouvre les options de lecture : instance, fichier/version et reprise. Le test de l'API HTTP et des droits de lecture est disponible sur les instances enregistrées dans **Paramètres**. Les erreurs de lancement restent visibles dans la fenêtre de lecture. Un envoi réussi signifie que Kodi a accepté la commande ; l'accès au fichier dépend encore de la configuration et des identifiants NAS de Kodi.

Un navigateur ne fournit pas de lecture SMB/NFS native : un lien vers ces protocoles dépend d'un gestionnaire installé sur le poste et de ses accès au partage. `file://` est aussi restreint depuis une page web. Aucun lien de lecture locale universel n'est proposé ; le chemin peut être ouvert dans un lecteur disposant des droits NAS. Cette version ne diffuse/transcode pas les fichiers vidéo via HTTP. [Gestionnaires de protocoles dans les navigateurs](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/registerProtocolHandler) · [Player.Open Kodi](https://kodi.wiki/view/JSON-RPC_API/v13.11).

## Accès local sans jeton

Par défaut, l'authentification reste obligatoire. Pour autoriser explicitement votre réseau local en IPv4/IPv6, adapter ces plages dans `.env` :

```dotenv
LOCAL_AUTH_NETWORKS='["192.168.1.0/24"]'
TRUSTED_PROXY_NETWORKS=[]
```

Les clients de ces plages accèdent directement au front et à l'API avec les droits administrateur, sans saisir de jeton. Les clients hors plage continuent d'utiliser `API_TOKEN`/`API_READ_TOKEN` (le premier reste obligatoire). Un Bearer explicitement incorrect est refusé, et un Bearer de lecture conserve ses droits restreints, même depuis le LAN. L'accès local ne crée pas de cookie réutilisable hors du réseau autorisé. Pour protéger les écritures du navigateur contre les requêtes provenant d'autres sites, le front récupère automatiquement un jeton **CSRF** via `GET /api/v1/auth/session` ; les clients locaux sans Bearer doivent le transmettre dans `X-CSRF-Token` pour écrire. Il ne remplace pas le jeton d'accès externe.

Le Docker désactive la réécriture automatique de l'adresse client par Uvicorn (`--no-proxy-headers`). En accès direct, seule l'adresse du pair TCP compte ; `X-Forwarded-For`, `X-Real-IP` et `Forwarded` envoyés par le client ne l'autorisent pas. Selon le réseau Docker/NAS, le pair observé peut être une passerelle : **ne pas autoriser cette passerelle pour tous les clients**.

### Docker/Synology masque l'adresse du PC

Si `sudo docker compose exec library printenv LOCAL_AUTH_NETWORKS` affiche la bonne plage mais que `GET /api/v1/auth/session` est refusé avec une adresse comme `172.25.0.1` dans les logs, l'application reçoit l'adresse de la passerelle Docker. En accès direct au NAS Linux/Synology, utiliser le fichier autonome `compose.host.yaml` pour supprimer cette traduction d'adresse. Il ne se superpose pas à `compose.yaml` : en réseau `host`, le serveur écoute directement le port `PORT` du NAS (8099 par défaut), sans publication de port Docker.

Ajouter à `.env` :

```dotenv
COMPOSE_FILE=compose.host.yaml:compose.dns.yaml
LOCAL_AUTH_NETWORKS='["192.168.1.0/24"]'
TRUSTED_PROXY_NETWORKS=[]
```

Puis lancer `sh ./rebuild.sh`. Le script et les commandes ordinaires `sudo docker compose ...` utilisent cette sélection de fichiers ; les prochaines mises à jour la conservent. Ouvrir ensuite directement `http://IP_DU_NAS:8099` et recharger la page. Les clients hors de la plage restent soumis au jeton. `PORT` et `BIND_ADDRESS` sont conservés, ainsi que les données et les paramètres de sécurité du conteneur. Si des volumes de pochettes ont été ajoutés dans `compose.yaml`, les reporter dans `compose.host.yaml` avant de changer de mode. Pour revenir au mode bridge, remplacer la ligne par `COMPOSE_FILE=compose.yaml:compose.dns.yaml` et relancer le script.

Un reverse proxy masque lui aussi l'adresse du navigateur : le mode `host` ne remplace pas sa configuration. Si le site est ouvert via un domaine/proxy, tester d'abord l'URL directe ci-dessus, puis déclarer uniquement le proxy réellement utilisé comme décrit ci-dessous.

Si un reverse proxy est réellement utilisé, déclarer uniquement son adresse exacte, par exemple `TRUSTED_PROXY_NETWORKS='["172.18.0.2/32"]'`, et le configurer pour remplacer ou compléter correctement `X-Forwarded-For` avec l'adresse de son client. Le back-end traverse la chaîne de droite à gauche jusqu'au premier pair non approuvé. Une chaîne absente, invalide ou composée uniquement de proxys approuvés impose le jeton. Les plages `/0` sont refusées. Ne pas déclarer un ensemble de clients ordinaires comme proxys approuvés. Avec un lancement Uvicorn personnalisé, conserver impérativement `--no-proxy-headers` ; en entrée HTTPS derrière proxy, configurer également `COOKIE_SECURE=true`.

## Application native iOS / iPadOS

Le dossier [`ios/`](ios/) fournit une application **SwiftUI native** utilisant directement l'API REST v1 : catalogue (pochettes/liste), recherche et filtres cumulables, fiches et épisodes, données Kodi/CLZ, notes et état vu, casting, bandes-annonces, listes personnelles, contrôle Kodi et administration des sources et imports. Le serveur Docker et l'interface web sont conservés. Le jeton Bearer est stocké dans le trousseau iOS. Voir [les instructions XcodeGen et la connexion sécurisée](ios/README.md).
