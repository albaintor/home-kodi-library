# <img src="home_media_catalog/static/cinema.svg" width="36" alt=""> Home Media Catalog

**Bring your Kodi library and physical movie collection together.**

Home Media Catalog combines a responsive, self-hosted **web application** with a native **iOS/iPadOS app**. The iOS app can manage a catalogue **standalone, without installing a server**. A Docker server is optional for a shared catalogue, web access and AI features; it runs on Linux, macOS/Windows with Docker Desktop, a home server or a NAS.

**Status (October 2026):** active development. Feature availability depends on the app version, media format and source configuration. See [iOS release preparation](#ios-release-preparation).


## Highlights

- **Combine** Kodi and CLZ Movies, MyMovies, or Filmotech (XML imports), with editions grouped by title.
- **Explore** movies, TV shows and episodes with posters, cast, formats, advanced filters and optional TMDB metadata.
- **Organize** your collection using custom lists, personal ratings and watched status.
- **Play** selected titles on an existing Kodi player, with advanced playback controls and media information.
- **Play** supported Kodi video paths, locally saved files, network shares and OneDrive media on iPhone, subject to source connectivity, codecs and permissions.
- **Access** your collection on iOS/iPadOS alone, or via a shared server and web interface.
- **Search with AI (optional, server required)** using natural language, hybrid semantic/keyword matching over film, series and episode synopses, and conversational refinements.
- **Choose standalone or server mode:** in standalone mode the iOS app imports and synchronizes Kodi directly, without Docker. In server mode it synchronizes catalogue metadata with the server while Kodi remote commands are sent directly from the iPhone.

Collection-only catalogs do not require Kodi.

### Web interface

<img width="500" alt="image" src="https://github.com/user-attachments/assets/5194ecd5-8360-4c87-bc23-7610ca5eb354" />

<img width="400" alt="image" src="https://github.com/user-attachments/assets/29fcdd5e-4424-4a29-bdbc-96fa26ee25f3" />




### iOS interface

<img width="300" alt="image" src="https://github.com/user-attachments/assets/d104a967-75d8-412f-9a92-2b184563fdde" />
<img width="300" alt="image" src="https://github.com/user-attachments/assets/01d21701-3361-4055-8f92-3906b409841b" />
<img width="300" alt="image" src="https://github.com/user-attachments/assets/16a8a792-6931-42de-934c-65f4947ef564" />
<img width="250" alt="image" src="https://github.com/user-attachments/assets/c74639b9-a45e-4f1c-87f0-9998220aecbc" />



## Native iOS/iPadOS experience and AI search

**Home Media Catalog for iOS/iPadOS** provides ordinary catalogue search **inside the main poster/list view** on iPhone and iPad. An optional sparkles action in the top toolbar opens a separate conversational AI search page when a configured server supports it. Search results and follow-up context remain available when returning from media details.

The current native Xcode project targets **iOS/iPadOS 18.0 or later**. Standalone catalogue import, browsing and direct Kodi remote control do not require the Docker server. AI search and shared web/iOS catalogue synchronization do require a compatible server.

On the Web and iOS, user can select a language model (only OpenAI at this time) and embeddings
model, securely save the AI key on the server. Indexed film, series and episode synopses and credited people power incremental
semantic retrieval, including role-specific cast/director/composer searches,
numeric rating filters, and matched episode season/number and plot excerpts.
A cached in-memory HNSW index accelerates large vector collections.
AI is entirely optional; no key or indexing is needed for ordinary browsing.

AI receives the query and, during indexing, title/genre/plot metadata,
credited actors/directors/composers/writers and available ratings to produce
embeddings; the index and credentials remain on the self-hosted server.

To enable AI, open **Settings → Artificial Intelligence**. Configure the AI language
model, embeddings model and API key, save, test both connections, and index
your catalogue summaries. The monthly USD spending cap is configurable.
The server stores the key; ordinary catalogue browsing does not require AI. See the
[full setup, privacy and API guide](docs/ai-search.md).


## Recent playback and iOS features (October 2026)

The project now includes **more than collection management**:

- **Web video playback:** network location browser for SMB/FTP/SFTP and configured NFS mounts, HTTP Range streaming, thumbnails sampled from actual videos, and FFmpeg-powered progressive MP4 transcoding of selected non-browser-friendly formats. Browser formats, transcoding quality and seek behaviour depend on source, codec and server CPU.
- **Cloud media:** OneDrive is currently the only supported integrated provider (including Device Code sign-in for private NAS deployments). Other cloud connectors are not exposed until supported. OneDrive OAuth tokens remain encrypted on the server. Apple's iCloud Drive remains accessible through **iOS Files**, not a server-side iCloud connector.
- **Native iOS playback:** AVPlayer with an optional PrismCore/FFmpeg-assisted route, video technical information and offline video files. Importing from Files copies the selected file to app storage; it is not automatically a zero-copy third-party stream.
- **Experimental iOS MEMC:** output targets of 48/60 fps using Apple VideoToolbox or Vision/Metal optical flow with artifact-protection settings. Not equivalent to SVPflow/RIFE; MEMC may produce artifacts, and **does not guarantee original Dolby Vision metadata preservation**. Native playback remains available with MEMC off.
- **Catalogue/sync:** improved Kodi synchronisation and cleanup, JSON backups, multi-source libraries, personalised lists and optional conversational AI search.


### iOS evaluation, purchase and support

The iOS/iPadOS client implements **10 free full-screen video playback sessions** for local files, NAS/SMB, cloud and network sources, including experimental MEMC modes. After those sessions, a **single non-consumable StoreKit purchase** is intended to unlock the native video player, including MEMC. Kodi remote control, cataloguing and (with a configured server/provider) AI search remain outside the paid video-player gate. Purchase availability and regional pricing depend on the actual App Store Connect product; this is **not** a claim that an in-app purchase has been approved or published.


The native **Settings → Support** section links to this project's
[home page](https://github.com/albaintor/home-media-catalog) and
[issue tracker](https://github.com/albaintor/home-media-catalog/issues).
The main collection context menu uses **Play on…** to choose a configured Kodi instance; after opening playback, the app navigates to its Kodi remote tab.


### Technology credits

Video playback uses third-party components, including [FFmpeg](https://ffmpeg.org/), [PrismCore](https://github.com/Wenzlik/PrismCore) and [MPVKit](https://github.com/Wenzlik/MPVKit). Their licences and documentation are available from the respective projects.

## Get started

### iOS/iPadOS without a server

1. Build/install the native client from the [iOS project](https://github.com/albaintor/home-media-catalog-ios) (App Store availability is not asserted here), and choose **standalone mode**.
2. Add a Kodi instance via local-network discovery or its hostname/IP address. Enable and configure Kodi's **HTTP JSON-RPC remote control**. Import the library directly from Kodi; an external MariaDB database is **not required** for the normal JSON-RPC import.
3. Alternatively, import a compatible CLZ Movies, My Movies or Filmotech XML collection. Configure media playback sources separately as needed. AI search and browser access are unavailable without a server.

### Shared web + iOS/iPadOS catalogue (optional)

1. [**Install the Docker server**](docs/installation.md) on your preferred host.
2. In the web interface, add a Kodi HTTP/JSON-RPC instance or import a supported collection XML. An optional **read-only MariaDB/MyVideos fallback** can help preserve additional Kodi editions when JSON-RPC is insufficient.
3. Pair the iOS/iPadOS client with a short-lived code from **Settings → iOS Pairing**. Keep the server and app at mutually supported API/data-model versions. Use HTTPS or a trusted VPN outside the local network.

## Documentation

- [Native iOS/iPadOS project and standalone setup](https://github.com/albaintor/home-media-catalog-ios)
- [Server installation, configuration, updates and troubleshooting](docs/installation.md)
- [iOS / Xcode Cloud setup, SwiftPM recovery and signing](docs/ios-xcode-cloud.md)
- [iOS StoreKit purchases, trial and Debug reset](docs/ios-storekit.md)
- [Features and usage](docs/features.md) · [AI search and model setup](docs/ai-search.md)
- [Home Assistant access](docs/home-assistant.md)
- [REST API](docs/api.md) · [OpenAPI schema](docs/openapi-v1.json)
- [Data model and backups](docs/model.md) · [Development and testing](docs/development.md)

Home Media Catalog now includes an **optional network/cloud media browser and video playback workflow**. The browser can transcode some remote video formats with FFmpeg; this consumes server CPU and is not equivalent to direct/native playback. Native iOS playback uses AVPlayer and PrismCore. Use **HTTPS or a VPN** for remote access.

[Support the project on Buy Me a Coffee](https://www.buymeacoffee.com/albaintor)

## iOS release preparation

Before making the app available, verify the build and signing on a physical device, video playback and network sources, the StoreKit purchase and restoration flow, and the current iOS screenshots. Configure the App Store Connect listing and privacy-policy link for the submitted version.

## Support

If you have any issues check first if it has not already been reported then [raise a ticket](https://github.com/albaintor/home-media-catalog/issues)



### CLZ, MyMovies and Filmotech imports

In Settings, select the collection type, choose an existing collection (or create
one), and upload its XML export. When paired with a server, the native iOS application uses the same import
endpoints: `POST /api/v1/imports/clz`, `/imports/mymovies`, and `/imports/filmotech`. In standalone mode, compatible XML is imported on-device instead.
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
after 24 hours. JSON-RPC is the preferred import path where supported; MariaDB
is an optional read-only fallback, and may be required to preserve additional
editions not exposed by JSON-RPC.

Automatic imports also have a **durable 24-hour attempt cooldown**. If Kodi
or the configured MariaDB fallback is unavailable, the failed job's
`created_at` in the persistent jobs table prevents repeated imports on
every app or server restart. Manual **Synchronize** and **Scan Kodi**
actions are unaffected. The server does not initiate a Kodi library scan as
part of the automatic refresh.

A successful import updates the source's `last_sync`. The jobs feed shows
the latest attempt and any failure; this can be used to distinguish a simple
startup freshness check from a full import.
