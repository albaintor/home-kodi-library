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
