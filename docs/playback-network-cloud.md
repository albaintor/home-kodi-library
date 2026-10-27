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

Providers: Google Drive, OneDrive, Dropbox, Box and pCloud.
iCloud Drive uses **native Files integration**, with no third-party OAuth.

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

### Other providers and the HTTPS OAuth callback

Configure these environment variables on the server:

- `CLOUD_OAUTH_REDIRECT_URL=https://your-domain/api/v1/playback/cloud/callback`
  (a public HTTPS address reachable from the iPhone's browser; it must
  exactly match an authorized redirect URI in each provider's application).
- `CLOUD_OAUTH_CLIENTS`: a JSON object keyed by provider, with a
  `client_id` and, where required, a `client_secret`. Example:
  `{"google_drive":{"client_id":"...","client_secret":"..."},"dropbox":{"client_id":"..."}}`.
- `PLAYBACK_ENCRYPTION_KEY`: optional but recommended, a URL-safe base64
  Fernet key generated using
  `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
  Without it, the key is derived from `API_TOKEN`; rotating `API_TOKEN`
  without a dedicated encryption key makes stored credentials unreadable.

The admin endpoint `POST /api/v1/playback/cloud/{location_id}/authorize`
returns an authorization URL. After the browser obtains consent, the server
exchanges the OAuth code and stores encrypted tokens **only in its SQLite
database**. `GET /api/v1/playback/cloud/status` never exposes tokens, and
`DELETE /api/v1/playback/cloud/{location_id}` disconnects that account locally.

An authorization attempt expires after 10 minutes and cannot survive a server
restart before the provider callback. Disconnecting on the server does not
revoke the external grant; manage provider-side authorizations separately.

**Current scope:** OAuth sign-in, encrypted storage and automatic token
refresh, read-only browsing, thumbnail retrieval and HTTP Range streaming
through temporary tickets for Google Drive, OneDrive, Dropbox, Box and
pCloud. Cloud files are not automatically synchronized into offline storage.
Real-world testing is required for each provider and account, particularly
CDN redirects, quotas and token refresh.

## Multiple accounts per provider

Each cloud account is a **separate playback location**, identified by a UUID.
The provider (`google_drive`, `dropbox`, `onedrive`, etc.) is never used
as an account key. Several accounts of the same provider can be connected
simultaneously, each with independent encrypted OAuth credentials.
Reconnecting and disconnecting affect only the selected location.

On the **web**, open **Settings → Network and cloud → Cloud services**,
then use **Add account** for Google Drive, Dropbox or OneDrive.
Each account is listed independently, with its email when the provider
makes one available, and individual **Reconnect** / **Disconnect** controls.

On **iOS**, open **Cloud services → provider → Add another account**.
Server-managed accounts are available to the web client and iOS devices
connected to that server. Accounts **authorized directly on the iPhone**
are separate: their credentials are stored per account in the iOS Keychain
and are not shared with the server. Existing native authorizations remain
recognizable after upgrading the app.

### Google Drive

1. In Google Cloud Console, enable the **Google Drive API**, configure the
   OAuth consent screen and request
   `https://www.googleapis.com/auth/drive.readonly`.
2. Create **OAuth 2.0 credentials → Web application**, registering the exact
   redirect URI `https://your-domain/api/v1/playback/cloud/callback`.
3. Under the catalogue's web settings,
   **Advanced → Cloud OAuth application configuration**, save the
   `client_id`, `client_secret` and HTTPS redirect for **Google Drive**.
   You can then authorize as many Google accounts as needed.
4. Files, folders and metadata are accessed read-only. Native Google
   Docs/Sheets files are not binary video files and cannot be played
   by the media player.

**Important:** `drive.readonly` is a restricted Google OAuth scope.
Depending on your application's publishing status, Google may require
test users, OAuth verification and a security assessment. Without the
necessary configuration, Google can block consent independently of the
iOS and web implementations.

### Dropbox

1. Create a **Scoped access** app in the Dropbox App Console.
2. Enable **files.metadata.read** and **files.content.read** and register
   `https://your-domain/api/v1/playback/cloud/callback` as an exact
   OAuth redirect URI.
3. In the catalogue's web settings, save the **App key** as Dropbox's
   `client_id` (and the App secret where applicable), then use
   **Add account** to authorize multiple Dropbox accounts.
4. OAuth requests a renewable token (`token_access_type=offline`).
   Browsing and streaming are read-only; the web browser never receives
   the OAuth token stored on the NAS.

For both providers, **iOS can use server-managed OAuth accounts**.
Direct native OAuth is also available with separate public OAuth clients
configured at build time in `OAuth.local.xcconfig`.
A web OAuth client cannot be used as a native iOS client without
provider-specific configuration.

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
- **Other providers with server-managed OAuth:** iOS invokes
  `POST /api/v1/playback/cloud/{id}/authorize?return_to=ios` using
  a provider-registered HTTPS callback.
- **Standalone iOS option:** direct connections to Microsoft Graph
  and other cloud providers require public native OAuth clients
  embedded in `OAuth.local.xcconfig` at build time. The native
  sign-in option is hidden unless its client is configured.
- **iOS Files fallback:** extensions supplied by the OneDrive, Dropbox
  and Google Drive apps use their own authentication. An item selected
  in Files can be imported for **offline playback**, without an OAuth
  identity owned by Home Media Catalog. Importing is not direct streaming.
- **OneDrive without a domain:** in Microsoft Entra, enable public client
  flows and delegated `Files.Read` access. Device Code sign-in needs
  no client secret or HTTPS redirect. Other providers still require
  their own OAuth registrations and HTTPS callbacks.

**Current limitation:** saving a location descriptor does not guarantee
that it can be browsed. SMB, FTP, NFS, UPnP and cloud browsing/streaming
adapters are separate from the configuration screens.
