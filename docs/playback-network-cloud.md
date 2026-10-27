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
(`host`, `port`, `share`, `username`, `workgroup`). Passwords, when provided,
are encrypted server-side using Fernet. The API only returns `has_password`.
The iOS app does **not** cache server secrets for offline use.
Updating a source without a new password preserves the existing one.

Persisting a source configuration does not itself implement SMB, NFS or FTP
directory browsing. SMB files can also be selected using the iOS Files app,
then imported locally for playback without the Home Media Catalog server.

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
specified by Microsoft. Tokens are encrypted on the NAS and never sent
to the browser or phone.

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
connected to that server. Accounts **authorized directly on the iPhone**
are separate: their credentials are stored per account in the iOS Keychain
and are not shared with the server. Existing native authorizations remain
recognizable after upgrading the app.

## Hybrid architecture (iOS and web)

- Addresses, ports, share paths and names are **source descriptors**
  centralized on the server and cached on the device for standalone use.
  If the locations API is not yet deployed (HTTP 404), iOS stores new
  descriptors locally and queues them for later synchronization.
- **Passwords entered on iOS** remain in the iPhone Keychain. They are
  **not** sent as part of synchronized source descriptors. The server
  may store a **different credential** in its encrypted database,
  entered independently via its web settings.
- **iOS OneDrive (recommended server-managed route):** the server uses
  Microsoft's Device Code flow without a public domain or redirect.
  The app displays the code, the user grants `Files.Read`, and iOS
  polls the server at the specified interval. Browsing and streaming
  then use authenticated API endpoints and temporary HTTP Range tickets.
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

**Current limitation:** saving a location descriptor does not guarantee
that it can be browsed. SMB, FTP, NFS, UPnP and cloud browsing/streaming
adapters are separate from the configuration screens.
