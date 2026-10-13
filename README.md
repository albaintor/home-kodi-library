# <img src="kodi_library/static/cinema.svg" width="36" alt=""> Media Catalog

**Bring your Kodi library and physical movie collection together.**

Media Catalog is a self-hosted catalog with a responsive **web interface** and a native **iOS/iPadOS client**. Its Docker server can run on a Linux machine, macOS/Windows with Docker Desktop, a home server or a NAS — no particular hardware is required.

## Highlights

- **Combine** Kodi (read-only MariaDB synchronization) and CLZ Movies (XML import), with editions grouped by title.
- **Explore** movies, TV shows and episodes with posters, cast, formats, advanced filters and optional TMDB metadata.
- **Organize** your collection using custom lists, personal ratings and watched status.
- **Play** selected titles on an existing Kodi player, including playback resume.
- **Access** the same catalog on the web, iOS/iPadOS or through Home Assistant.

The server uses its own **SQLite database**. Importing Kodi content requires an existing external Kodi MyVideos MariaDB/MySQL database, but browsing does not require a Kodi player to be running. CLZ-only catalogs do not require Kodi.

### Web interface

<img width="500" alt="image" src="https://github.com/user-attachments/assets/5194ecd5-8360-4c87-bc23-7610ca5eb354" />

<img width="400" alt="image" src="https://github.com/user-attachments/assets/29fcdd5e-4424-4a29-bdbc-96fa26ee25f3" />




### iOS interface


## Get started

1. [**Install the Docker server**](docs/installation.md) on your preferred host.
2. Open the web interface to add a Kodi source or import a CLZ Movies XML export.
3. Connect the **Media Catalog** iOS/iPadOS app with a short-lived pairing code from the web interface.

## Documentation

- [Server installation, configuration, updates and troubleshooting](docs/installation.md)
- [Features and usage](docs/features.md)
- [Home Assistant access](docs/home-assistant.md)
- [REST API](docs/api.md) · [OpenAPI schema](docs/openapi-v1.json)
- [Data model and backups](docs/model.md) · [Development and testing](docs/development.md)

Media Catalog is not a media streaming/transcoding service. Use **HTTPS or a VPN** for remote access.

[Support the project on Buy Me a Coffee](https://www.buymeacoffee.com/albaintor)
