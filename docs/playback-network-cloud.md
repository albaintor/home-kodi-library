# Network and cloud sources

## Local discovery

The iOS app discovers SMB/NFS/FTP/SFTP services using Bonjour on the local network.
The server exposes `GET /api/v1/playback/discovery` to discover UPnP SSDP announcements
on **the server's own local network** (the scan is limited to 1.8 seconds and does
not fetch advertised URLs). Discovery may return no results in an isolated
Docker bridge network or where multicast is blocked. Allow local-network
access in iOS Settings.

## Configuring a source

Protocol-specific properties are stored in the server's SQLite database
(`host`, `hostname`, `port`, `share`, `username`, `workgroup`).
For SMB and NFS, `hostname` is an optional **Kodi path alias**; `host`
remains the actual IP address/network target used for connections. Passwords, when provided,
are encrypted server-side using Fernet. The API only returns `has_password`.
Paired administrator iOS devices can now provision server-owned SMB, FTP and
SFTP passwords through a dedicated endpoint over HTTPS. The app stores them
only in its device-only Keychain, not in the cached location JSON or logs.
Updating a source without a new password preserves the existing one.

**Server-side SFTP authentication:** password changes submitted from iOS while
the paired server is connected over HTTPS are also encrypted server-side.
Existing iOS-only Keychain secrets are not exported silently until the source
is saved or an offline edit is replayed. The server uses password authentication
for SFTP, not SSH agent or private-key authentication.
Missing/rejected server credentials return HTTP 409 with an actionable error.
The server's SSH host-key pin is stored in `/data/ssh/known_hosts`.


SMB, FTP, SFTP and NFS have separate read-only browsing adapters. Native
NFS uses `libnfs14` in the server image and does **not** require an OS mount.
Installations with an existing `PLAYBACK_NFS_MOUNTS` mapping keep their
mounted-path implementation for compatibility. All remote file paths are
relative to their configured export roots; server streaming uses HTTP Range
tickets to support seeks. On iOS, SMB and NFS playback can stream directly
without Kodi through localhost HTTP Range bridges.

Example: a Kodi path `nfs://NAS/volume1/Media/film.mkv` matches a source
with IP `192.168.1.20`, optional hostname `NAS`, and export
`/volume1/Media`. The app mounts the export and reads `film.mkv`.
The configured export must be available to the NFS client; server-side
export permissions (NFSv3/v4, UID/GID and firewall) still apply.

## Network-first playback of Kodi editions in the web player

For `Read in browser` on a Kodi film/episode, the server first compares the
canonical Kodi file URL with its saved SMB, NFS, FTP or SFTP locations.
The protocol, host (including the optional Kodi hostname alias), port and
configured root must agree. When several roots match, the most specific wins.
The server opens the file with **its own saved network credentials**, not Kodi's,
and keeps HTTP Range seeking, track analysis, subtitles and remux/transcoding on
that same source. If the network connection is inaccessible, the server tries
Kodi's HTTP VFS as a fallback. For nonmatching paths, Kodi VFS is used directly.

A powered-off Kodi instance does not prevent browser playback through an
already matched network location when the catalogue includes its file path.
Choosing `Read on Kodi` still sends Player.Open to the selected Kodi instance,
so that action requires Kodi online. The web playback selector shows blue
(online), red (offline) or white (checking) status icons based on a server-side
Kodi JSON-RPC reachability check; an idle instance counts as online.


## OAuth connections

Only **OneDrive** is currently enabled as an integrated cloud provider in the web and iOS interfaces. Other connector code is retained for future work but its providers are not selectable. iCloud Drive remains available through the separate **Apple Files integration**, without third-party OAuth in Home Media Catalog.

### OneDrive without a public domain (recommended method)

Microsoft Entra application already registered for Home Media Catalog:
`145e06bb-b35b-4d96-8cf8-4077cd5e5528` (a public client ID, **not a secret**).
The server uses it by default. Override it using `ONEDRIVE_CLIENT_ID`
or the provider's advanced settings.

In **Microsoft Entra → App registrations → Authentication**, enable
**Allow public client flows: Yes**. Supported account types must include
personal Microsoft accounts. Under **API permissions**, add
**Microsoft Graph → Delegated permissions → Files.Read**.
This flow requires no client secret, redirect URI or public Internet domain.

From either the web interface or iOS, select
**OneDrive → Connect without a domain**. The server requests a temporary
code from Microsoft; the user enters it at
`https://microsoft.com/devicelogin`, and the interface polls at the interval
specified by Microsoft. Tokens are encrypted on the NAS. For a paired administrator iPhone using
HTTPS, a public-client OneDrive grant can additionally be provisioned to the
iPhone Keychain for direct Microsoft Graph browsing and playback. Tokens never
appear in ordinary location lists, web browser authorization responses or
portable catalogue backups.

The NAS needs **outbound Internet access**; no incoming port needs to be
opened. An in-progress verification does not survive a server restart.

Admin endpoints: `POST /api/v1/playback/cloud/{id}/device/start`, followed
by `POST /api/v1/playback/cloud/{id}/device/poll` with `request_id`.
The server respects `interval` and `slow_down`, expires requests and never
returns the Microsoft `device_code` or access tokens to the client.

### Optional OneDrive HTTPS OAuth callback

The recommended OneDrive Device Code flow above works without a public domain.
For installations using an HTTPS OAuth callback instead, the server accepts:

- `CLOUD_OAUTH_REDIRECT_URL=https://your-domain/api/v1/playback/cloud/callback` (must match the redirect URI registered in Microsoft Entra).
- `CLOUD_OAUTH_CLIENTS`: for example, `{"onedrive":{"client_id":"..."}}`, with a client secret where applicable.
- `PLAYBACK_ENCRYPTION_KEY`: optional but recommended; use a URL-safe
  Fernet key generated with
  `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
  Without a dedicated key, rotating `API_TOKEN` makes stored credentials unreadable.

`POST /api/v1/playback/cloud/{location_id}/authorize` returns a URL for the
optional browser authorization flow. Tokens are kept encrypted in SQLite.
`GET /api/v1/playback/cloud/status` never exposes them, and
`DELETE /api/v1/playback/cloud/{location_id}` disconnects the selected account
locally. OAuth authorization attempts expire after 10 minutes or a server restart.

**Supported scope:** OneDrive read-only browsing, thumbnails and HTTP Range
streaming through temporary tickets. Cloud files are not automatically cached
for offline playback. Other cloud connectors are not supported in this release.

## Multiple accounts per provider

Each cloud account is a **separate playback location**, identified by a UUID.
The provider name (`onedrive`) is never used
as an account key. Several accounts of the same provider can be connected
simultaneously, each with independent encrypted OAuth credentials.
Reconnecting and disconnecting affect only the selected location.

On the **web**, open **Settings → Network and cloud → Cloud services**,
then use **Add account** for OneDrive.
Each account is listed independently, with its email when the provider
makes one available, and individual **Reconnect** / **Disconnect** controls.

On **iOS**, open **Cloud services → provider → Add another account**.
Server-managed accounts are available to the web client and iOS devices
connected to that server. Public-client accounts authorized through the server can be copied per
location to the iOS Keychain for direct access; standalone iOS-only OAuth
accounts remain separate unless explicitly connected through the server.
A confidential-client web authorization cannot be transferred without its
client secret: reauthorize that account using the public device-code flow
to enable direct iOS access.

## Hybrid architecture (iOS and web)

- Addresses, ports, share paths and names are **source descriptors**
  centralized on the server and cached on the device for standalone use.
  If the locations API is not yet deployed (HTTP 404), iOS stores new
  descriptors locally and queues them for later synchronization.
- **Network passwords** remain outside synchronized source descriptors.
  When connected through HTTPS, an iOS edit sends the secret separately
  for encrypted storage on the server; an administrator iPhone can provision
  saved server credentials into the local device-only Keychain.
  When the API is unavailable, the last cache and Keychain values survive.
- **iOS OneDrive (recommended server-managed route):** Microsoft device-code
  authorization is performed once. A paired administrator iPhone on HTTPS
  can copy the same public-client OAuth authorization into its Keychain,
  then browse Microsoft Graph and stream directly from OneDrive without
  proxying bytes through the NAS. Token refreshes are serialized per iOS
  account. Other clients may still use server proxy/ticket fallback.
- **Optional OneDrive HTTPS OAuth:** the server can also use a registered
  HTTPS callback when one is configured; Device Code is recommended.
- **Standalone iOS OneDrive:** direct Microsoft Graph connections require
  a public native OAuth client configured in `OAuth.local.xcconfig`.
  The direct sign-in option is hidden unless that client is configured.
- **iOS Files fallback:** Files can use any installed File Provider extension
  to import a file for **offline playback**. Files authorization is separate
  from Home Media Catalog cloud integrations. Importing is not direct streaming.
- **OneDrive without a domain:** enable public client flows and delegated
  `Files.Read` permission in Microsoft Entra. No client secret or public
  HTTPS redirect is required for Device Code sign-in.

**Current limitation:** a saved descriptor or a password does not guarantee
that the destination is reachable from the iPhone. The exact same direct
connector is used on and off the home network, so private IP/hostname
destinations require suitable network routing (for example, VPN).
Credentials are only provisioned from the server over HTTPS.

A missing or temporarily unreachable server does not erase the previously
cached server location list, server cloud account display state, or Keychain.
The direct network and OneDrive connectors can work without a server API.
The admin-only provisioning endpoints are
`GET /api/v1/playback/locations/{id}/device-credentials` and
`GET /api/v1/playback/cloud/{id}/device-credentials`. Both return
`Cache-Control: no-store`, and regular source endpoints remain redacted.

A saved descriptor does not guarantee that the NAS NFS and SMB require reachable servers and valid exports,
shares and permissions. UPnP and unsupported cloud providers have no native
browsing/streaming adapters.
