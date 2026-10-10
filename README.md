# Home Kodi Library

Catalogue indépendant pour consulter une bibliothèque Kodi et une collection physique CLZ Movies. Un conteneur Docker expose une interface web responsive et une **API REST versionnée**, utilisable directement par une future application iOS. Home Assistant peut servir de point d'entrée via Ingress.

## Première version

- Recherche libre dans les titres, synopsis, genres, collections et certaines métadonnées ; recherche sans distinction d'accents, avec correspondance par préfixe de mots.
- Filtres combinables : film/série/épisode, Kodi/CLZ, genre, format, collection, vu/non vu/en cours/inconnu, ajouts des 30 derniers jours. L'API ajoute année, source précise et série parente.
- Tri par titre, date d'ajout, année, note ou dernier visionnage ; ordre croissant/décroissant et pagination.
- Vue pochettes ou liste, fiche détaillée, notes, synopsis, éditions physiques et fichiers Kodi, reprise et historique de lecture.
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

Adapter `PUID`/`PGID` et le propriétaire du dossier aux comptes du NAS si nécessaire. Ouvrir `http://IP_DU_NAS:8099`, puis saisir `API_TOKEN`. Dans **Connexions**, ajouter la source Kodi et/ou importer un export XML CLZ. Les mises à jour Kodi sont ensuite automatiques, toutes les 300 secondes par défaut. Un nouvel export CLZ remplace uniquement les entrées de la collection CLZ choisie.

Pour MariaDB, utiliser un compte avec `SELECT` sur la base `MyVideos…`. Renseigner explicitement la base en cas de plusieurs bibliothèques. Sans nom, le connecteur sélectionne la version numérique accessible la plus élevée ; toutes les vues doivent être accessibles avec ce compte. Les connexions et mots de passe sont enregistrés dans le volume local, jamais renvoyés par l'API.

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
uvicorn kodi_library.app:create_app --factory --port 8099
```

```bash
pytest -q
ruff check kodi_library tests
ruff format --check kodi_library tests
```

Les tests utilisent un XML synthétique et des connexions simulées ; aucun export privé, mot de passe ni base Kodi ne figure dans le dépôt. La première validation réelle de MariaDB, de Docker sur le NAS et de hass_ingress reste à réaliser sur l'installation cible. Ce dépôt fournit un service Docker autonome, pas encore une intégration HA installable ni un add-on Supervisor.
