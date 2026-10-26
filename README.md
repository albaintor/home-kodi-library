# <img src="home_media_catalog/static/cinema.svg" width="36" alt=""> Home Media Catalog



**Bring your Kodi library and physical movie collection together.**

Home Media Catalog is a self-hosted catalog with a responsive **web interface** and a native **iOS/iPadOS client**. Its Docker server can run on a Linux machine, macOS/Windows with Docker Desktop, a home server or a NAS — no particular hardware is required.

## Renommage Home Media Catalog et compatibilité

Le dépôt se nomme `home-media-catalog`, le module Python
`home_media_catalog` (anciennement `kodi_library`) et la distribution
Python `home-media-catalog`. Les commandes Docker et les fichiers de
déploiement ont été mis à jour.

Une mise à jour NAS conserve le service Compose `library`, le volume
`./data:/data`, les clés de la configuration `.env`, les jetons, les
appairages et les chemins d’API `/api/v1/…`. L'identifiant de service
`home-kodi-library`, le format des sauvegardes JSON `home-kodi-library`
et les clés de préférences navigateur `kodi_library_preferences_v1` /
`kodi_library_view` sont volontairement conservés pour garantir la
compatibilité de l’application iOS et la conservation des données.

Le nom Docker par défaut devient `home-media-catalog`, avec prise en
charge de `HOME_MEDIA_CATALOG_CONTAINER_NAME` et de l'ancienne variable
`KODI_LIBRARY_CONTAINER_NAME`. L'image se nomme
`home-media-catalog:local`. Pour reconstruire une installation existante,
utiliser `docker compose up -d --build --force-recreate library` puis
vérifier `/healthz`, sans supprimer `.env` ou `./data`.

Les modules Kodi, par exemple `connectors/kodi.py`, gardent leur nom :
ils désignent une intégration métier.


## Highlights

- **Combine** Kodi (read-only MariaDB synchronization) and CLZ Movies, MyMovies, or Filmotech (XML imports), with editions grouped by title.
- **Explore** movies, TV shows and episodes with posters, cast, formats, advanced filters and optional TMDB metadata.
- **Organize** your collection using custom lists, personal ratings and watched status.
- **Play** selected titles on an existing Kodi player, including playback resume.
- **Access** the same catalog on the web, iOS/iPadOS or through Home Assistant.
- **Search with AI (optional)** using natural language, hybrid semantic/keyword matching over film, series and individual episode synopses, and conversational refinements.

The server uses its own **SQLite database**. Importing Kodi content requires an existing external Kodi MyVideos MariaDB/MySQL database, but browsing does not require a Kodi player to be running. Collection-only catalogs do not require Kodi.

### Web interface

<img width="500" alt="image" src="https://github.com/user-attachments/assets/5194ecd5-8360-4c87-bc23-7610ca5eb354" />

<img width="400" alt="image" src="https://github.com/user-attachments/assets/29fcdd5e-4424-4a29-bdbc-96fa26ee25f3" />




### iOS interface

<img width="300" alt="image" src="https://github.com/user-attachments/assets/d104a967-75d8-412f-9a92-2b184563fdde" />
<img width="300" alt="image" src="https://github.com/user-attachments/assets/01d21701-3361-4055-8f92-3906b409841b" />
<img width="300" alt="image" src="https://github.com/user-attachments/assets/16a8a792-6931-42de-934c-65f4947ef564" />
<img width="250" alt="image" src="https://github.com/user-attachments/assets/c74639b9-a45e-4f1c-87f0-9998220aecbc" />



## What's new — iOS 1.2 and AI search

**Home Media Catalog for iOS/iPadOS 1.2** keeps the standard
SwiftUI search field **inside the main catalogue**, directly filtering the
same poster/list view on iPhone, iPad and iPhone Duo. A sparkles action in
the **top toolbar** opens a dedicated conversational AI page through native
navigation, without a popup or a separate Search tab. Its results and follow-up
context survive opening a movie or episode and returning. Media details are
retrieved directly by ID; optional TMDB enrichment no longer blocks the
first display. Strict semantic verification filters unrelated style matches.

On the Web and iOS, an administrator can select a language model and embeddings
model, securely save the OpenAI key on the server, and **test both connections**
independently. Indexed film, series and episode synopses and credited people power incremental
semantic retrieval, including role-specific cast/director/composer searches,
numeric rating filters, and matched episode season/number and plot excerpts.
A cached in-memory HNSW index accelerates large vector collections.
AI is entirely optional; no key or indexing is needed for ordinary browsing.

OpenAI receives the query and, during indexing, title/genre/plot metadata,
credited actors/directors/composers/writers and available ratings to produce
embeddings; the index and credentials remain on the self-hosted server. See
[AI search setup, data privacy and API endpoints](docs/ai-search.md).

### Conversational AI: refine, expand, undo

After an initial AI search, use follow-up prompts to **filter existing titles**
("Retire Valérian de la liste"), **add more matching titles** without losing
the current selection ("Ajoutes-en d'autres"), or **start a different search**
("Cherche plutôt des films de Miyazaki"). On iOS, **Undo last refinement**
restores the previous result list and conversation; **New search** clears them.
An administrator can add up to the 60 currently displayed AI results to a new
or existing list.

To enable AI, open **Settings → AI search** on iOS or **Settings →
Recherche intelligente · IA** on the web. Configure the OpenAI language
model, embeddings model and API key, save, test both connections, and index
your catalogue summaries. The monthly USD spending cap is configurable.
The server stores the key; ordinary catalogue browsing does not require AI.
Recent result-editing features require the `ai_refine_results` server capability;
the client falls back to plan-only refinement on earlier servers. See the
[full setup, privacy and API guide](docs/ai-search.md).

## Get started

1. [**Install the Docker server**](docs/installation.md) on your preferred host.
2. Open the web interface to add a Kodi source or import a CLZ Movies, MyMovies, or Filmotech XML export.
3. Connect the **Home Media Catalog** iOS/iPadOS app with a short-lived pairing code from the web interface.

## Documentation

- [Server installation, configuration, updates and troubleshooting](docs/installation.md)
- [Features and usage](docs/features.md) · [AI search and model setup](docs/ai-search.md)
- [Home Assistant access](docs/home-assistant.md)
- [REST API](docs/api.md) · [OpenAPI schema](docs/openapi-v1.json)
- [Data model and backups](docs/model.md) · [Development and testing](docs/development.md)

Home Media Catalog is not a media streaming/transcoding service. Use **HTTPS or a VPN** for remote access.

[Support the project on Buy Me a Coffee](https://www.buymeacoffee.com/albaintor)

## Support

If you have any issues check first if it has not already been reported then [raise a ticket](https://github.com/albaintor/home-media-catalog/issues)






### CLZ, MyMovies and Filmotech imports

In Settings, select the collection type, choose an existing collection (or create
one), and upload its XML export. The native iOS application uses the same import
endpoints: `POST /api/v1/imports/clz`, `/imports/mymovies`, and `/imports/filmotech`.
MyMovies refers to the XML collection export from My Movies Collection Management;
Filmotech uses its XML film export, rather than a database backup or PDF report.

Each provider has a distinct icon in Settings, catalog cards and movie editions.
Imports replace only the selected collection. Matching IMDb/TMDb identifiers (or
an unambiguous title and year across providers) group editions on the same record.
Invalid, empty, unsafe XML and duplicate entry IDs leave the previous collection
intact. Local cover paths are still subject to the server's artwork access rules;
exporting XML does not upload separate image files.

The parser fixtures in `tests/fixtures/filmotech.xml` and `mymovies.xml` are
representative test documents. Validate an export from your application version
before replacing a large collection; provider-specific XML variants may differ.


### Automatic Kodi/MariaDB catalogue refresh

The server imports an empty catalogue once when possible. Later, opening
the web app or connecting from iOS requests a `/api/v1/sources/sync-stale`
check. A successful Kodi-source import is eligible for automatic refresh only
after 24 hours.

Automatic imports also have a **durable 24-hour attempt cooldown**. If Kodi
or the configured MariaDB fallback is unavailable, the failed job's
`created_at` in the persistent jobs table prevents repeated imports on
every app or server restart. Manual **Synchronize** and **Scan Kodi**
actions are unaffected. The server does not initiate a Kodi library scan as
part of the automatic refresh.

A successful import updates the source's `last_sync`. The jobs feed shows
the latest attempt and any failure; this can be used to distinguish a simple
startup freshness check from a full import.
