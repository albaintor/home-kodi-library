# <img src="home_media_catalog/static/cinema.svg" width="36" alt=""> Home Media Catalog



**Bring your Kodi library and physical movie collection together.**

Home Media Catalog is a self-hosted catalog with a responsive **web interface** and a native **iOS/iPadOS client**. Its Docker server can run on a Linux machine, macOS/Windows with Docker Desktop, a home server or a NAS — no particular hardware is required.

## Home Media Catalog naming and compatibility

The repository is named `home-media-catalog`, the Python module is
`home_media_catalog` (previously `kodi_library`), and the Python
distribution is `home-media-catalog`. Docker commands and deployment
files have been updated accordingly.

Upgrading an existing NAS installation preserves the `library` Compose
service, the `./data:/data` volume, `.env` configuration keys, API
tokens, paired devices, and `/api/v1/…` routes. The
`home-kodi-library` service identifier and JSON backup format, along
with the `kodi_library_preferences_v1` and `kodi_library_view` browser
preference keys, are intentionally retained for iOS compatibility and
data preservation.

The default Docker container name is now `home-media-catalog`.
Both `HOME_MEDIA_CATALOG_CONTAINER_NAME` and the legacy
`KODI_LIBRARY_CONTAINER_NAME` environment variables are supported.
The image is named `home-media-catalog:local`. To rebuild an existing
installation, run `docker compose up -d --build --force-recreate library`
and check `/healthz`; do not remove `.env` or `./data`.

Kodi-specific modules, such as `connectors/kodi.py`, keep their names
because they identify the actual Kodi integration.


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



## Native iOS/iPadOS experience and AI search

**Home Media Catalog for iOS/iPadOS** keeps the standard
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


## Recent playback and iOS features (October 2026)

The project now includes **more than collection management**:

- **Web video playback:** network location browser for SMB/FTP/SFTP and configured NFS mounts, HTTP Range streaming, thumbnails sampled from actual videos, and FFmpeg-powered progressive MP4 transcoding of selected non-browser-friendly formats. Browser formats, transcoding quality and seek behaviour depend on source, codec and server CPU.
- **Cloud media:** OneDrive (including a Device Code flow for private NAS deployments), Google Drive and Dropbox; other providers have connector infrastructure. Cloud OAuth secrets/tokens stay encrypted on the server. Apple's iCloud Drive is accessed through **iOS Files**, not a server-side iCloud connector.
- **Native iOS playback:** AVPlayer with an optional PrismCore/FFmpeg-assisted route, video technical information and offline video files. Importing from Files copies the selected file to app storage; it is not automatically a zero-copy third-party stream.
- **Experimental iOS MEMC:** output targets of 48/60 fps using Apple VideoToolbox or Vision/Metal optical flow with artifact-protection settings. Not equivalent to SVPflow/RIFE; MEMC may produce artifacts and **does not guarantee original Dolby Vision metadata preservation**. Native playback remains available with MEMC off.
- **Catalogue/sync:** improved Kodi synchronisation and cleanup, JSON backups, multi-source libraries, personalised lists and optional conversational AI search.

Read the **public** [Xcode Cloud / iOS build instructions](docs/ios-xcode-cloud.md), [StoreKit / evaluation guide](docs/ios-storekit.md), and [network/cloud playback guide](docs/playback-network-cloud.md). The native source repository is private; these guides are public.

### iOS evaluation, purchase and support (development version)

The iOS/iPadOS client offers **10 free full-screen video playback sessions**
for local files, NAS/SMB, cloud and network sources, including experimental
MEMC modes. Remaining sessions are displayed under the video title while
the on-screen controls are visible. The **11th playback session** requires
one **non-consumable lifetime purchase** to unlock the entire native video
player, including MEMC. The planned French price is **€2** (actual price
and availability are provided by the App Store through StoreKit 2). Kodi
remote control, cataloguing and AI search stay free.

The in-app product identifier is
`fr.albaintor.HomeKodiLibrary.video_player_unlock` because the underlying
app bundle identifier is intentionally preserved across the rename. The
purchase will not be available until it has been configured in
App Store Connect; a missing product produces an unavailable state and a
retry control, not a fake price. Purchase and restore are not claims of
App Store approval.

The native **Settings → Support** section links to this project's
[home page](https://github.com/albaintor/home-media-catalog) and
[issue tracker](https://github.com/albaintor/home-media-catalog/issues).
The main collection context menu uses **Play on...** (French:
**Lire sur...**) with a generic media-playback icon; this still launches
the Kodi player selector.

#### Local iOS build and package resolution

The native iOS source repository is private. For developers with access,
after updating its `main` branch, quit Xcode and run:

```bash
cd /path/to/home-media-catalog-ios
git switch main
git pull --ff-only
brew install xcodegen
zsh scripts/resolve_local_packages.sh
```

This corrects `Missing package product 'AMSMB2'` and
`Missing package product 'PrismCore'` caused by a stale/unresolved package
graph: it prepares checksum-verified FFmpeg frameworks, generates the
**local** Xcode project, then runs `xcodebuild -resolvePackageDependencies`.
Reopen `HomeMediaCatalog.xcodeproj` when done. If required, use
**File → Packages → Reset Package Caches** and **Resolve Package Versions**.

The committed `Package.resolved` pins the two **remote** SwiftPM
dependencies: **AMSMB2 4.0.3** and **Swift Atomics 1.2.0**. The iOS
Xcode project currently references **local** `Vendor/FFmpegApple`
as the PrismCore package, which is created by the helper script.
This directory is not committed to Git; avoid committing generated
Xcode changes after a local rebuild.

#### Xcode Cloud configuration

In Xcode Cloud, grant its GitHub App access to the private
`albaintor/home-media-catalog-ios` repository. Use `main`,
the shared `HomeMediaCatalog` scheme, an Xcode release supporting
Swift 6, and automatic signing using the correct Apple Developer team.
Keep these identifiers (the marketing rename does not alter signing):

| Capability | Identifier |
| --- | --- |
| iOS app | `fr.albaintor.HomeKodiLibrary` |
| Share extension | `fr.albaintor.HomeKodiLibrary.MediaShareExtension` |
| App Group | `group.fr.albaintor.HomeMediaCatalog` |
| iCloud | `iCloud.fr.albaintor.HomeMediaCatalog` |

The checked-in executable `ci_scripts/ci_post_clone.sh` installs XcodeGen
if necessary, repairs and verifies downloaded FFmpeg frameworks, and
regenerates the local Xcode project **before package resolution**.
The full [public Xcode Cloud troubleshooting guide](docs/ios-xcode-cloud.md)
covers the GitHub permission flow, dependency cache problems, code signing,
the package lockfile and build validation.

#### StoreKit and the 10 evaluation sessions

Under **App Store Connect → Monetization → In-App Purchases**, add a
**non-consumable** product to the existing app (Bundle ID
`fr.albaintor.HomeKodiLibrary`):

| Property | Value |
| --- | --- |
| Reference name | Home Media Catalog — Video Player |
| Product ID | `fr.albaintor.HomeKodiLibrary.video_player_unlock` |
| Type | Non-Consumable, lifetime |
| France price point | €2 (or nearest offered Apple price point) |
| English product name | Unlock video player |
| French product name | Débloquer le lecteur vidéo |

Complete the localizations, descriptions, review screenshot, tax/banking
agreements and review details. Sandbox and TestFlight validation are
required before distribution. The app reads **StoreKit's real localized
price**; missing App Store Connect configuration leaves the buy action
unavailable. The **11th playback session** requires purchase; catalogue
and Kodi remote playback remain free.

#### Reset trial sessions while developing

In a **Debug build installed with Xcode** select **Settings → Video player
access → Reset evaluation trials** and confirm. This restores **10 free
sessions** by deleting the UserDefaults trial count and the precise
Keychain backup; it does **not** revoke or reset a StoreKit purchase.
The control is not compiled into Release/TestFlight/App Store builds.
Deleting and reinstalling the app alone may preserve its Keychain counter.

Read the full public [StoreKit and developer testing guide](docs/ios-storekit.md)
for restoring purchases, cancelled or pending transactions, 10-session
counting, and physical-device testing.

**Distribution status:** a successful SwiftPM/Xcode build is not a
licensing certificate; FFmpeg/MPVKit dependency licences, relinking
requirements, device archives, StoreKit approval and testing remain
release gates.

### Legal notices and third-party licences

Home Media Catalog includes third-party components, including the **FFmpeg** executable in the Docker/server image and **PrismCore, MPVKit and FFmpeg** libraries in the native iOS dependency chain. FFmpeg's LGPL/GPL obligations depend on the **actual build**, not on whether the app is free or paid. PrismCore's own App Store exception **does not extend to FFmpeg**. Earlier iOS/MPVKit builds used statically linked FFmpeg components with LGPLv3 obligations. The current development branch uses an experimental dynamically linked FFmpeg adapter; it still requires a pre-release source, binary, licensing and distribution-rights audit.

See [Third-party notices and distribution checklist](THIRD_PARTY_NOTICES.md). No statement here should be read as certification that a particular iOS binary or Docker release is compliant. Also review rights to media content, artwork, logos and third-party service APIs before distribution.

## Get started

1. [**Install the Docker server**](docs/installation.md) on your preferred host.
2. Open the web interface to add a Kodi source or import a CLZ Movies, MyMovies, or Filmotech XML export.
3. Connect the **Home Media Catalog** iOS/iPadOS app with a short-lived pairing code from the web interface.

## Documentation

- [Server installation, configuration, updates and troubleshooting](docs/installation.md)
- [iOS / Xcode Cloud setup, SwiftPM recovery and signing](docs/ios-xcode-cloud.md)
- [iOS StoreKit purchases, trial and Debug reset](docs/ios-storekit.md)
- [Features and usage](docs/features.md) · [AI search and model setup](docs/ai-search.md)
- [Home Assistant access](docs/home-assistant.md)
- [REST API](docs/api.md) · [OpenAPI schema](docs/openapi-v1.json)
- [Data model and backups](docs/model.md) · [Development and testing](docs/development.md)

Home Media Catalog now includes an **optional network/cloud media browser and video playback workflow**. The browser can transcode some remote video formats with FFmpeg; this consumes server CPU and is not equivalent to direct/native playback. Native iOS playback uses AVPlayer and PrismCore. Use **HTTPS or a VPN** for remote access.

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
