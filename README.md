# Home Kodi Library

An independent media catalog for browsing a Kodi library and a physical CLZ Movies collection. A Docker container provides a responsive web interface and a **versioned REST API**, also used by the native iOS application. Home Assistant can serve as an entry point through Ingress.

**Support development:** [Buy Me a Coffee](https://buymeacoffee.com/albaintor).

## Features

- Title-only search (localized and original titles) by default; optional advanced search across synopses, genres, collections, cast, and other metadata. Searches are accent-insensitive and support word-prefix matching.
- Combinable filters: movies/TV shows/episodes, Kodi/CLZ, genre, video/audio/disc formats (multiple selections combined with AND), collection, watched/unwatched/in progress/unknown, and additions from the past 30 days. The API also supports release year, a specific source, and parent TV series. Video resolution **4K** is distinct from physical **4K UHD / Blu-ray 4K** discs. Filter and sort preferences are saved in the browser.
- Sort by title, date added, year, rating, or last watched, in ascending or descending order. Infinite scrolling loads batches of 60 items, with a fallback button and recovery after errors.
- Poster grid and list views; detailed media pages with ratings, synopses, physical editions, Kodi files, resume positions, and playback history.
- Responsive details with Kodi cast members and roles, resolution and 3D badges, Dolby Vision/HDR10/HDR10+, Dolby/DTS audio, and physical media formats. Per-edition technical details remain available through the API.
- **Read-only** synchronization from Kodi MariaDB, covering movies, TV shows, episodes, IMDb/TMDB/TVDB identifiers, and alternative versions when exposed by `movie_view`.
- CLZ Movies XML import: editions, Blu-ray/4K/3D/DVD formats, barcodes, box sets, languages, subtitles, and `seenhistory`.
- Kodi/CLZ matching using reliable external identifiers and media type, with a conservative fallback to unambiguous title **and** year matches. Editions remain attached to their respective sources.
- Multiple Kodi HTTP/JSON-RPC players associated with a MariaDB source. Play or resume on an **already running** Kodi instance without relying on Home Assistant entities.
- MariaDB and Kodi connection tests before adding a source: networking, authentication, MyVideos database, read permissions, and Kodi HTTP control. These tests are also available through the API.
- Persistent local SQLite database and artwork cache: **no second MariaDB server**.
- Administrator token, optional read-only token, browser sessions with CSRF protection, and a separate Ingress secret.

The web interface uses exactly the same API as other clients. No essential server-side business logic lives exclusively in the frontend.

## Getting started with Docker on a NAS

```bash
git clone https://github.com/albaintor/home-kodi-library.git
cd home-kodi-library
cp .env.example .env
openssl rand -hex 32
```

Copy the generated token into `API_TOKEN` in `.env`. Generate a **second** token for `INGRESS_TOKEN` if Home Assistant will provide access to the catalog. `API_READ_TOKEN` is optional and must also be distinct.

```bash
mkdir -p data
sudo chown 1000:1000 data
chmod 700 data
chmod 600 .env
docker compose up -d --build
```

Adjust `PUID`/`PGID` and the directory owner to match your NAS accounts if necessary. Open `http://NAS_IP:8099` and enter `API_TOKEN`. Under **Settings**, add a Kodi source and/or import a CLZ XML export. Kodi synchronizes automatically, every 300 seconds by default. A new CLZ export only replaces records belonging to the selected CLZ collection.

For MariaDB, use an account with `SELECT` permission on the `MyVideos…` database. Specify the database explicitly if you have several libraries. If no name is supplied, the connector chooses the highest accessible numbered version; the account must have access to all required views. Connections and passwords are saved in the local volume and are never returned by the API.

After an update that adds metadata support, use **Settings → Synchronize** for Kodi and reimport the XML into the **existing CLZ collection**. HDR and Atmos badges require explicit information from the source; a UHD disc or a TrueHD track alone is not evidence of these technologies.

### Updating an existing installation

`rebuild.sh` fetches `main` using `git pull --ff-only`, builds the image using host networking for Python package installation, and recreates the `library` service while retaining `compose.dns.yaml` and DNS settings in `.env`. It waits up to 120 seconds for the container health check and prints logs if startup fails.

```bash
# First time, to retrieve the update script:
git pull --ff-only
# For subsequent updates:
sh ./rebuild.sh
```

Run the script as the user who owns the repository; it automatically invokes `sudo` for Docker if needed. Do not run the entire script with `sudo`, which could change Git file ownership. The `.env` file and `data/` directory are preserved. Git conflicts or build failures stop the script before it recreates the container. It does not run `down`, reset Git, or delete data or images. The script uses the Compose files described above; add any additional files your installation needs to `compose_run`.

Public HTTP(S) posters are downloaded and cached. SMB/NFS images or local file paths must be mounted read-only and declared in `ARTWORK_MOUNTS`; see [the data model and artwork documentation](docs/model.md). Unavailable posters display a readable placeholder.

## Troubleshooting DNS errors during Docker builds

A `Temporary failure in name resolution` message during `pip install` means the build environment cannot resolve the package server. A subsequent `No matching distribution found` error does not necessarily mean the requested package version is unavailable.

On a Linux or Synology NAS, try building with host networking:

```bash
sudo docker build --network=host -t home-kodi-library:local .
sudo docker compose up -d --no-build
```

The first command uses host networking while building the image. The second starts the previously built image using the service networking defined in `compose.yaml`.

A Compose override is provided to retain this setting for future builds:

```bash
git pull --ff-only
sudo docker compose -f compose.yaml -f compose.build-host.yaml up -d --build
```

`build.network` applies to Dockerfile `RUN` instructions. Adding `dns:` to a service affects running containers and does not, by itself, fix DNS during the build. See the [Docker documentation](https://docs.docker.com/reference/compose-file/build/#network).

If host networking also fails, check name resolution first on the NAS and then from a container:

```bash
nslookup pypi.org
sudo docker run --rm python:3.12-slim python -c 'import socket; print(socket.getaddrinfo("pypi.org", 443))'
sudo docker run --rm --dns 1.1.1.1 --dns 8.8.8.8 python:3.12-slim python -c 'import socket; print(socket.getaddrinfo("pypi.org", 443))'
```

The last command uses public DNS servers to help identify a Docker DNS issue. If only this command succeeds, the NAS/Docker DNS configuration needs attention. Service-level DNS configuration may also be necessary for downloading posters at runtime.

## Troubleshooting remote poster downloads

CLZ poster URLs are imported from the XML export and downloaded by the container. Fixing **build-time** DNS (`build.network: host` or `docker build --network=host`) does not configure DNS for the running container.

If Docker DNS resolution fails at runtime, use the optional Compose override to set explicit DNS servers:

```bash
git pull --ff-only
sudo docker compose -f compose.yaml -f compose.dns.yaml up -d --no-build --force-recreate library
```

It uses `1.1.1.1` and `8.8.8.8` by default. To use your router/resolver and preserve local hostname resolution, set `DNS_PRIMARY` and `DNS_SECONDARY` in `.env`. Keep `-f compose.dns.yaml` in future `up`/recreate commands so this configuration is retained. You can also combine it with `compose.build-host.yaml` for rebuilds.

Test a real URL from your CLZ export inside the container:

```bash
sudo docker compose exec library python -c 'import urllib.request; u="https://clzmovies.r.sizr.io/core/covers/lg/f4/f4_1259989_0_DungeonsDragonsHonorAmongThiev.jpg"; r=urllib.request.urlopen(u, timeout=15); print(r.status, r.headers.get("Content-Type"), r.geturl()); r.close()'
```

A response such as `200 image/jpeg` confirms that direct downloads work. DNS, certificate, or HTTP errors indicate what is blocking the request. This command is for network diagnostics; the application's image proxy also applies security checks to URLs.

For Kodi artwork stored on SMB/NFS/local paths, configure `ARTWORK_MOUNTS` as described in the [data model documentation](docs/model.md). DNS settings alone cannot grant file access.

## Home Assistant and iOS access

- [Home Assistant access through hass_ingress](docs/home-assistant.md), including remote access through the same Home Assistant URL.
- [API contract, authentication, and curl examples](docs/api.md).
- [OpenAPI v1 schema](docs/openapi-v1.json), generated from the server; interactive Swagger documentation is available at `/docs` after web login.
- [Sample Swift client using URLSession](examples/KodiLibraryClient.swift).
- [Data model, watched/unwatched rules, matching, and backups](docs/model.md).
- [Initial validation results and limitations](docs/validation.md).

To access Docker directly from outside your LAN, use a VPN or an HTTPS URL through a reverse proxy. Remote Home Assistant access does not automatically expose the API for a standalone iOS client. The client needs its own server URL and Bearer token; the Ingress secret is reserved for the Home Assistant proxy.

## Development and testing

Requires Python 3.12 or later and a single server process. Sessions and import jobs are local to that process; do not configure multiple workers.

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

Browser testing uses a synthetic catalog without personal data:

```bash
pip install -e '.[dev,browser]'
playwright install chromium
PYTHONPATH=. python tests/browser/check_catalogue.py
```

Tests use synthetic XML and simulated connections. No private export, password, or Kodi database is included in the repository. Real-world MariaDB, NAS Docker, and hass_ingress configuration must be validated on the target installation. The repository provides a standalone Docker service, not an installable Home Assistant integration or Supervisor add-on.

## Cast, portraits, and trailers

Media details distinguish cast, directing, writing, production, composing, cinematography, and other credits. Kodi provides roles, directors/writers, and portraits already available in its database. CLZ-exported credits/portraits and YouTube links are imported when present. The original sample export has empty cast sections: images visible in CLZ Cloud are not included in that XML.

To enrich movies and TV shows automatically with TMDB, place the **API Read Access Token** from [your TMDB account](https://www.themoviedb.org/settings/api) in `.env`, then run `sh ./rebuild.sh`. An **API v3 key** is also accepted in the same setting. Your TMDB account password is not valid here.

```dotenv
TMDB_API_TOKEN=your_TMDB_read_access_token
```

Under **Settings → Metadata and portraits · TMDB**, the UI reports whether TMDB is configured and offers **Test TMDB connection**. Invalid credentials, network problems, and rate limits produce explicit errors. Each media page also indicates when TMDB is unavailable. Enabling TMDB does not require reimporting the catalog: reopening a media page is sufficient when it already has an IMDb/TMDB identifier.

An administrator opening a media page triggers enrichment using the title's IMDb/TMDB identifier. The process fetches cast, crew, portraits, and YouTube trailers without fuzzy title matching. **Names and portraits** link to TMDB/IMDb profiles; otherwise an explicitly labeled **IMDb search** link is provided. The YouTube button appears at the top of the media page: it opens the only trailer directly, lets you choose when several are available, or searches YouTube if no trailer was supplied.

Metadata persists in SQLite across restarts and synchronization. It is reused for 30 days and refreshed at the next opening afterward. **Refresh TMDB metadata** forces an update and invalidates affected cached images; if a request fails, previously saved information is retained. Portraits use the same authenticated image proxy, 30-day/512 MiB cache, and security checks as posters. API clients can request enrichment independently of the web frontend. TMDB is optional: imported or cached content remains accessible without network access or a TMDB token.

The browser remembers search terms, filters, sort direction, view mode, and other preferences. Facets that no longer exist in the catalog are ignored on reload. Background catalog scrolling is locked while media details or a list are open and its position is restored on close; nested dialogs have independent scrolling.

## Ratings, watch status, and lists

At the top of each media page, the star control lets you set **your personal rating out of 10** using hover, click, or keyboard input; a star or half-point slider saves immediately. The **Watched / Unwatched** badge toggles and saves the status on click without a separate Save button. These personal values are shared by users of the installation, persist in SQLite, and take precedence over imports. Clearing a rating or choosing **From sources** restores the imported value. Public IMDb/Kodi ratings, play counts, and source files remain imported information: personal edits do not write back to MariaDB or CLZ. No additional database server is required.

In the catalog, the **Custom lists** section offers **＋ Create a list** and **Open my lists**. **My lists** also remains accessible in the header. Creating a list opens the name form directly; the same form appears on first opening when no list exists. Administrator access is required to create or edit lists; read-only access displays an explanation in the dialog.

**My lists** supports creating, renaming, and deleting lists; searching for **movies or TV shows** to add via **＋ Add media**; removing items; and moving them with ↑/↓ in **Manual order, ascending** mode. A media page also has a **＋ Add to list** button, including the option to create a list from that dialog; existing associated list names are clickable. The last opened list and its sorting preference are saved in the browser. Sorting by title, year, date added, public rating, or personal rating changes the view only and does not modify the saved manual order. Lists use pagination and revision numbers to prevent concurrent changes from silently overwriting a reordering operation. They are separate from imported CLZ collections and box sets.

Work UUIDs preserve list associations during normal synchronization. If a media item disappears from every source, it is removed from its lists; deleting a list does not delete any media. Upgrading automatically migrates SQLite from schema version 1 to version 2. Back up the `data` volume before downgrading: older releases cannot read schema version 2.

## Kodi file paths and playback

Full file paths are visible on media pages, with a **Copy path** action (on HTTP, a copy dialog is shown instead of clipboard access). **Play on Kodi** contacts the selected player from the backend and lets you select the file/version. Explicit selection opens that exact path, even for a default version. Kodi uses its own NAS credentials and file access.

If no player is associated with the source, the media page offers **Configure a Kodi player**. The Kodi-logo button opens playback options: player, file/version, and resume position. HTTP API and file-read permission tests are available for saved players under **Settings**. Playback launch errors remain visible in the playback dialog. A successful command means Kodi accepted it; access to the file still depends on Kodi's NAS configuration and permissions.

Web browsers do not provide native SMB/NFS playback. Opening these protocol links depends on a local protocol handler and its share permissions. `file://` links are also restricted from web pages. There is no universal local-playback link; users can open the path in a compatible player with NAS access. This release does **not** stream or transcode video files over HTTP. [Browser protocol handlers](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/registerProtocolHandler) · [Kodi Player.Open](https://kodi.wiki/view/JSON-RPC_API/v13.11).

## Stable container name on Synology

`compose.yaml` and `compose.host.yaml` define `container_name: ${KODI_LIBRARY_CONTAINER_NAME:-home-kodi-library}`, giving the container a recognizable name in Synology Container Manager on subsequent deployments. To customize it without editing tracked files, set `KODI_LIBRARY_CONTAINER_NAME` in `.env`. This does not affect volumes or the selected networking mode, although a Compose container-name change can require recreation. If another container already uses the name, deployment fails until the conflict is resolved. Preserve `COMPOSE_FILE` to keep host networking where it is used for token-free local access.

## Local access without a token

Authentication is required by default. To explicitly trust your local IPv4/IPv6 networks, configure their ranges in `.env`:

```dotenv
LOCAL_AUTH_NETWORKS='["192.168.1.0/24"]'
TRUSTED_PROXY_NETWORKS=[]
```

Clients in these ranges can access the frontend and API directly with administrator rights without entering a token. Clients outside them must still use `API_TOKEN`/`API_READ_TOKEN` (the former remains mandatory). An explicitly incorrect Bearer token is rejected, and a read-only Bearer token keeps its restricted permissions even on the LAN. Local access does not create a cookie that can be reused outside the allowed network. To protect browser writes against cross-site requests, the frontend automatically obtains a **CSRF** token through `GET /api/v1/auth/session`; tokenless local API clients must supply it in `X-CSRF-Token` for write operations. It does not replace the external access token.

Docker disables Uvicorn's automatic client-address rewriting (`--no-proxy-headers`). With direct access, only the TCP peer address counts; client-supplied `X-Forwarded-For`, `X-Real-IP`, and `Forwarded` headers cannot grant access. Depending on Docker/NAS networking, the observed peer may be a gateway: **do not trust that gateway on behalf of all clients**.

### Docker/Synology hides the computer's IP address

If `sudo docker compose exec library printenv LOCAL_AUTH_NETWORKS` shows the correct subnet but `GET /api/v1/auth/session` is rejected with an address such as `172.25.0.1` in the logs, the app is seeing the Docker gateway IP. For direct Linux/Synology NAS access, use the standalone `compose.host.yaml` to avoid that address translation. Do not layer it on top of `compose.yaml`: in `host` network mode, the server listens directly on the NAS `PORT` (8099 by default), without Docker port publishing.

Add to `.env`:

```dotenv
COMPOSE_FILE=compose.host.yaml:compose.dns.yaml
LOCAL_AUTH_NETWORKS='["192.168.1.0/24"]'
TRUSTED_PROXY_NETWORKS=[]
```

Then run `sh ./rebuild.sh`. The script and regular `sudo docker compose ...` commands use this selection of files and retain it for later updates. Open `http://NAS_IP:8099` directly and refresh the page. Clients outside the allowed subnet still need a token. `PORT`, `BIND_ADDRESS`, data, and container security settings are retained. If you added poster volumes to `compose.yaml`, copy those mounts into `compose.host.yaml` before switching modes. To return to bridged networking, change the line to `COMPOSE_FILE=compose.yaml:compose.dns.yaml` and rerun the script.

A reverse proxy also hides the browser's IP address: host networking does not replace proper proxy configuration. If you use a domain/proxy, test the direct URL above first, then trust only the actual proxy as described below.

When using a reverse proxy, declare only its exact address, for example `TRUSTED_PROXY_NETWORKS='["172.18.0.2/32"]'`, and configure it to correctly replace or append the real client IP in `X-Forwarded-For`. The backend walks the chain from right to left until it finds the first untrusted peer. Missing, invalid, or entirely trusted-proxy chains require a token. `/0` network ranges are rejected. Do not declare ordinary client subnets as trusted proxies. When running Uvicorn manually, keep `--no-proxy-headers` enabled; for HTTPS through a reverse proxy also set `COOKIE_SECURE=true`.

## Native iOS / iPadOS app

The [`ios/`](ios/) directory contains a **native SwiftUI app** that talks directly to REST API v1: catalog (posters/list), search and combinable filters, movies and episodes, Kodi/CLZ data, ratings and watch status, cast, trailers, personal lists, Kodi playback control, and administration of sources and imports. Docker and the web interface continue to work independently. The Bearer token is stored in the iOS Keychain. See the [XcodeGen build and secure connection instructions](ios/README.md).
