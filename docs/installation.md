# Install the Home Media Catalog server

[← Overview](../README.md) · [Features](features.md)

Home Media Catalog runs in Docker on **Linux, Windows or macOS with Docker Desktop**, and on compatible **home servers or NAS devices**. The NAS examples below are optional; no Synology or Home Assistant installation is required.

## Requirements

- Docker with Compose v2 (`docker compose`), Git, persistent storage, and network access to the host on port `8099` (configurable).
- For Kodi: an **existing external MyVideos MariaDB/MySQL database**, reachable from the server, with a user permitted to `SELECT` from the needed views. Kodi need not be running to browse.
- For CLZ Movies: an **XML export**; Kodi and MariaDB are not required.

## Install and start with Docker

```bash
git clone https://github.com/albaintor/home-media-catalog.git
cd home-media-catalog
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

On Linux, adjust `PUID`/`PGID` and the directory owner to match the host's account as needed. The `chown`/`chmod` commands above are Linux examples; on Docker Desktop, adapt permissions and directory sharing to macOS or Windows. Open `http://SERVER_IP:8099` and enter `API_TOKEN`. Under **Settings**, add a Kodi source and/or import a CLZ XML export. Kodi only synchronizes automatically when the web site or iOS app is opened **and** the last successful MariaDB import for that source is older than 24 hours (or missing). The empty-catalogue bootstrap can import a new source once. There is no recurring background scheduler; manually selecting **Synchronize** or **Scan Kodi** remains possible. A new CLZ export only replaces records belonging to the selected CLZ collection.

For MariaDB, use an account with `SELECT` permission on the `MyVideos…` database. Specify the database explicitly if you have several libraries. If no name is supplied, the connector chooses the highest accessible numbered version; the account must have access to all required views. Connections and passwords are saved in the local volume and are never returned by the API.

After an update that adds metadata support, use **Settings → Synchronize** for Kodi and reimport the XML into the **existing CLZ collection**. HDR and Atmos badges require explicit information from the source; a UHD disc or a TrueHD track alone is not evidence of these technologies.

**Permissions:** The default container account is UID/GID 1000. The `data/` directory must be writable by that account on Linux. On Windows/macOS, verify Docker Desktop volume sharing. Keep `.env` private, back up `data/`, and never commit credentials.

### Connect the iOS/iPadOS app

In the web interface, use **Settings → iOS Pairing** to generate a short-lived code. Home Media Catalog can discover the server over Bonjour on the local network or connect by manual URL. To advertise Bonjour, set `DISCOVERY_ENABLED=true` in `.env` and ensure mDNS reaches your network. On some Linux Docker hosts (including NAS), host networking helps; it is **not mandatory** for manual connection.

For access from outside your LAN, use HTTPS through a reverse proxy or a VPN. The iOS client needs its own authentication token; the Home Assistant Ingress secret is not for iOS.

## Updating an existing installation

`rebuild.sh` fetches `main` using `git pull --ff-only`, builds the image using host networking for Python package installation, and recreates the `library` service while retaining `compose.dns.yaml` and DNS settings in `.env`. It waits up to 120 seconds for the container health check and prints logs if startup fails.

```bash
# First time, to retrieve the update script:
git pull --ff-only
# For subsequent updates:
sh ./rebuild.sh
```

Run the script as the user who owns the repository; it automatically invokes `sudo` for Docker if needed. Do not run the entire script with `sudo`, which could change Git file ownership. The `.env` file and `data/` directory are preserved. Git conflicts or build failures stop the script before it recreates the container. It does not run `down`, reset Git, or delete data or images. The script uses the Compose files described above; add any additional files your installation needs to `compose_run`.

Public HTTP(S) posters are downloaded and cached. SMB/NFS images or local file paths must be mounted read-only and declared in `ARTWORK_MOUNTS`; see [the data model and artwork documentation](model.md). Unavailable posters display a readable placeholder.

## Network access and troubleshooting

The standard `compose.yaml` uses Docker bridge networking. `compose.host.yaml` provides an optional **Linux-only** host-networking setup, often useful when preserving the caller IP address for LAN authentication or Bonjour. `compose.dns.yaml` and `compose.build-host.yaml` are optional troubleshooting overrides.

## Container name

`compose.yaml` and `compose.host.yaml` define `container_name: ${KODI_LIBRARY_CONTAINER_NAME:-home-kodi-library}`, giving the container a consistent name in Docker tooling, including Synology Container Manager. To customize it without editing tracked files, set `KODI_LIBRARY_CONTAINER_NAME` in `.env`. This does not affect volumes or the selected networking mode, although a Compose container-name change can require recreation. If another container already uses the name, deployment fails until the conflict is resolved. Preserve `COMPOSE_FILE` to keep host networking where it is used for token-free local access.

## Local access without a token

Authentication is required by default. To explicitly trust your local IPv4/IPv6 networks, configure their ranges in `.env`:

```dotenv
LOCAL_AUTH_NETWORKS='["192.168.1.0/24"]'
TRUSTED_PROXY_NETWORKS=[]
```

Clients in these ranges can access the frontend and API directly with administrator rights without entering a token. Clients outside them must still use `API_TOKEN`/`API_READ_TOKEN` (the former remains mandatory). An explicitly incorrect Bearer token is rejected, and a read-only Bearer token keeps its restricted permissions even on the LAN. Local access does not create a cookie that can be reused outside the allowed network. To protect browser writes against cross-site requests, the frontend automatically obtains a **CSRF** token through `GET /api/v1/auth/session`; tokenless local API clients must supply it in `X-CSRF-Token` for write operations. It does not replace the external access token.

Docker disables Uvicorn's automatic client-address rewriting (`--no-proxy-headers`). With direct access, only the TCP peer address counts; client-supplied `X-Forwarded-For`, `X-Real-IP`, and `Forwarded` headers cannot grant access. Depending on Docker networking, the observed peer may be a gateway: **do not trust that gateway on behalf of all clients**.

### Docker bridge networking hides the client IP

If `sudo docker compose exec library printenv LOCAL_AUTH_NETWORKS` shows the correct subnet but `GET /api/v1/auth/session` is rejected with an address such as `172.25.0.1` in the logs, the app is seeing the Docker gateway IP. For direct access on a Linux Docker host (including a NAS), use the standalone `compose.host.yaml` to avoid that address translation. Do not layer it on top of `compose.yaml`: in `host` network mode, the server listens directly on the Docker host's `PORT` (8099 by default), without Docker port publishing.

Add to `.env`:

```dotenv
COMPOSE_FILE=compose.host.yaml:compose.dns.yaml
LOCAL_AUTH_NETWORKS='["192.168.1.0/24"]'
TRUSTED_PROXY_NETWORKS=[]
```

Then run `sh ./rebuild.sh`. The script and regular `sudo docker compose ...` commands use this selection of files and retain it for later updates. Open `http://SERVER_IP:8099` directly and refresh the page. Clients outside the allowed subnet still need a token. `PORT`, `BIND_ADDRESS`, data, and container security settings are retained. If you added poster volumes to `compose.yaml`, copy those mounts into `compose.host.yaml` before switching modes. To return to bridged networking, change the line to `COMPOSE_FILE=compose.yaml:compose.dns.yaml` and rerun the script.

A reverse proxy also hides the browser's IP address: host networking does not replace proper proxy configuration. If you use a domain/proxy, test the direct URL above first, then trust only the actual proxy as described below.

When using a reverse proxy, declare only its exact address, for example `TRUSTED_PROXY_NETWORKS='["172.18.0.2/32"]'`, and configure it to correctly replace or append the real client IP in `X-Forwarded-For`. The backend walks the chain from right to left until it finds the first untrusted peer. Missing, invalid, or entirely trusted-proxy chains require a token. `/0` network ranges are rejected. Do not declare ordinary client subnets as trusted proxies. When running Uvicorn manually, keep `--no-proxy-headers` enabled; for HTTPS through a reverse proxy also set `COOKIE_SECURE=true`.

## Troubleshooting DNS errors during Docker builds

A `Temporary failure in name resolution` message during `pip install` means the build environment cannot resolve the package server. A subsequent `No matching distribution found` error does not necessarily mean the requested package version is unavailable.

On a Linux Docker host (including a Synology NAS), try building with host networking:

```bash
sudo docker build --network=host -t home-media-catalog:local .
sudo docker compose up -d --no-build
```

The first command uses host networking while building the image. The second starts the previously built image using the service networking defined in `compose.yaml`.

A Compose override is provided to retain this setting for future builds:

```bash
git pull --ff-only
sudo docker compose -f compose.yaml -f compose.build-host.yaml up -d --build
```

`build.network` applies to Dockerfile `RUN` instructions. Adding `dns:` to a service affects running containers and does not, by itself, fix DNS during the build. See the [Docker documentation](https://docs.docker.com/reference/compose-file/build/#network).

If host networking also fails, check name resolution first on the Docker host and then from a container:

```bash
nslookup pypi.org
sudo docker run --rm python:3.12-slim python -c 'import socket; print(socket.getaddrinfo("pypi.org", 443))'
sudo docker run --rm --dns 1.1.1.1 --dns 8.8.8.8 python:3.12-slim python -c 'import socket; print(socket.getaddrinfo("pypi.org", 443))'
```

The last command uses public DNS servers to help identify a Docker DNS issue. If only this command succeeds, the Docker host's DNS configuration needs attention. Service-level DNS configuration may also be necessary for downloading posters at runtime.

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

For Kodi artwork stored on SMB/NFS/local paths, configure `ARTWORK_MOUNTS` as described in the [data model documentation](model.md). DNS settings alone cannot grant file access.

## Related guides

- [Home Assistant](home-assistant.md)
- [Data model and artwork paths](model.md)
- [REST API and authentication](api.md)
- [Development and tests](development.md)
