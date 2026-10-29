# Advanced network explorer (2026-10-29)

This feature is staged behind the feature branch until integration testing.

## Permission rules

| Client/mode | Search | Rename/move/delete | Copy |
|---|---|---|---|
| iOS standalone SMB | Yes | Yes, direct SMB2/3 | Yes, direct SMB2/3 |
| iOS standalone NFS | Yes | Yes, native NFSKit | Not offered (NFSKit has no public streaming file-copy primitive) |
| iOS standalone FTP/SFTP | Not currently accessible without server | Not implemented | Not implemented |
| iOS connected, administrator, SMB/NFS | Yes | Yes, native direct adapter | SMB yes; NFS direct no |
| iOS connected, administrator, FTP/SFTP | Yes, server API | Yes, via API | Yes, via API |
| iOS connected, read-only | Yes | No | No |
| Any OneDrive/cloud connector | Yes, subject to provider pagination | No | No |
| Web explorer | Yes, server API | Never exposed | Never exposed |

Note: the server can copy NFS through a **writable, explicitly configured** `PLAYBACK_NFS_MOUNTS` mount. The native NFS server connector without a mount remains read-only.

## Recursive search

- Search term must have 2–120 characters on the server; it is case insensitive.
- The server walks breadth-first from the selected folder with a hard cap of 160 folders, 8,000 directory entries and depth 12; results are pages of up to 80. A partial result is indicated as truncated.
- The iOS native browser walks up to 120 folders, 8,000 entries and 500 results, depth 12; visible cells render incrementally.
- The web browser has a local *this folder only* option and a recursive server-backed option.
- Neither implementation creates a persistent index. Server pagination deliberately recomputes the bounded traversal per page rather than retaining credentials or a server-side search session.

## Security boundaries

- Network edits are restricted to paths relative to the configured location; deletion of the location root, `..` traversal, folder-to-own-subfolder moves and overwrites are rejected.
- The web never invokes file mutations. A new REST endpoint enforces a dedicated **explicit administrator bearer token**. A local-network request (implicitly treated as admin by the legacy authentication mode) or browser session **is not enough**, even if it is labelled admin.
- Read/write rights on the remote SMB/NFS/FTP/SFTP share are still enforced by the remote filesystem and the supplied credentials.
- Delete is permanent. There is no trash/recycle-bin guarantee. Test first on a disposable share.
- Copying an entire directory can require substantial time and disk capacity on the remote share.

## Future web authentication (not included here)

Before enabling web writes, replace implicit local administrator privilege with opt-in authenticated sessions and role-based access control:

1. An administrator password stored as a slow password hash (Argon2id) with rate limiting, lockout policy and password reset/recovery strategy.
2. Session cookies with `HttpOnly`, `Secure` on HTTPS, explicit `SameSite` policy, expiry and CSRF validation on writes.
3. Administrators can read/write authorized locations; other users are read-only. Enforce roles in the API independently of the front end.
4. Explicit opt-in for write access per network source, server-side operation audit log, destination collision checks, delete confirmation, and ideally a recoverable trash.
5. LAN and reverse-proxy deployments must not silently bypass login. Ensure deployment uses TLS if exposed outside a trusted network.

The current feature intentionally keeps the web read-only.
