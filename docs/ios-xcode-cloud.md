# Xcode Cloud — Home Media Catalog iOS

## Why the previous build failed

The tracked `HomeMediaCatalog.xcodeproj` referenced the local package
`Vendor/FFmpegApple`, but `Vendor/` is ignored and does not exist in a
fresh Xcode Cloud checkout. This caused **Could not resolve package
dependencies** before compilation. The tracked project now references the
public PrismCore adapter from `https://github.com/albaintor/ffmpeg-apple.git`,
branch `prismcore-dynamic`.

The currently published r2 FFmpeg XCFrameworks have incorrect Clang module
metadata. To preserve the corrected local-development variant, Xcode Cloud
uses an executable `ci_scripts/ci_post_clone.sh` hook to:

1. Run `python3 scripts/prepare_dynamic_ffmpeg.py`; this clones the public
   adapter and downloads **SHA-256 verified** FFmpeg binary archives, then
   repairs Clang module maps without modifying the Mach-O library contents.
2. Install `xcodegen` with Homebrew if it isn't already available.
3. Run `xcodegen generate --spec project.ffmpeg-local.yml` so the generated
   project uses the freshly prepared local `Vendor/FFmpegApple` package.

This is a reproducible build workaround, **not** an App Store licence audit.

## GitHub access and Xcode Cloud setup

1. Open the **main** branch of `HomeMediaCatalog.xcodeproj` with an Xcode
   version supporting Swift 6. The shared scheme is **HomeMediaCatalog**,
   not the legacy HomeKodiLibrary scheme.
2. Select **Product → Xcode Cloud → Create Workflow**. Sign in with the Apple
   Developer team that owns the app, authorize the GitHub account and allow
   the **albaintor/home-media-catalog-ios** private repository in the
   Xcode Cloud GitHub App's **Repository access** settings. For personal
   GitHub repos, admin permission is required; organization repositories
   require owner authorization for initial setup.
3. Select source branch `main` and an appropriate iOS build, test or archive
   workflow. Confirm the `ci_post_clone.sh` step runs and succeeds **before**
   package resolution. The script needs public GitHub access for third-party
   XCFramework downloads and the public FFmpeg adapter.
4. Enable automatic signing using the correct Apple Developer team.
   Keep the existing app identifier `fr.albaintor.HomeKodiLibrary`,
   share extension identifier
   `fr.albaintor.HomeKodiLibrary.MediaShareExtension`, App Group
   `group.fr.albaintor.HomeMediaCatalog` and iCloud container
   `iCloud.fr.albaintor.HomeMediaCatalog`. Ensure the App Group/iCloud
   capabilities and provisioning profiles are configured in Certificates,
   Identifiers & Profiles. Rebranding the marketing name does **not**
   require changing the historical bundle IDs.
5. Use a compatible Xcode version and iOS SDK (deployment target iOS 18).
   Inspect the generated build's package resolution, framework embedding,
   signing and device archive errors independently. A simulator build cannot
   validate a signed distribution archive.
6. Preserve the complete committed `Package.resolved` at
   `HomeMediaCatalog.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved`.
   It pins the public PrismCore adapter, AMSMB2 4.0.3, and Swift Atomics
   1.2.0. Xcode Cloud uses this file for deterministic package resolution.
   After changing package versions, refresh and commit all relevant pins.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| `Vendor/FFmpegApple` missing | Confirm the latest `main` revision is selected and the post-clone hook runs before resolution. The **tracked** project should reference the remote adapter; the **generated** build project references the locally prepared variant. |
| `xcodegen` missing | Inspect Homebrew installation output in the post-clone log. Xcode Cloud includes Homebrew but scripts cannot use `sudo`. |
| `Libavutil`, module map or Clang import errors | Confirm the local repaired FFmpeg variant was generated rather than building directly with the published r2 package. |
| Missing library/framework at runtime | Inspect app `Frameworks/`, signing and `otool -L`; `AMSMB2.framework` must be embedded. |
| Signature/App Group/iCloud issues | Check signing team, app + share-extension profiles, matching entitlements and provisioning capabilities. |
| Private repo absent in Xcode Cloud | Update GitHub App repository access, then revisit the Xcode Cloud connection in App Store Connect/Xcode. |
| StoreKit product missing | Separate from package resolution. Configure the IAP via [App Store Connect instructions](ios-storekit.md). |

## Fix local Xcode errors: missing AMSMB2 and PrismCore

A fresh pull restores the tracked **remote** PrismCore package reference, while
some developer Macs were previously using the prepared *local* FFmpeg package.
The SwiftPM error `Missing package product 'AMSMB2'` or
`Missing package product 'PrismCore'` is often secondary to unresolved
source packages, missing product checkouts or a stale Xcode cache.
Both products are declared by their actual package manifests.

With Xcode **closed**, in the iOS private working copy:

```sh
git switch main
git pull --ff-only
brew install xcodegen
zsh scripts/resolve_local_packages.sh
```

The helper prepares the adapted PrismCore/FFmpeg Swift package, regenerates
the local project for PrismCore **and** AMSMB2, and executes
`xcodebuild -resolvePackageDependencies`. Reopen the Xcode project when
finished. If errors persist, use Xcode **File → Packages → Reset Package
Caches**, then **Resolve Package Versions**. Inspect the first package checkout
or network failure before diagnosing missing-product messages.

## Equivalent local build

```sh
brew install xcodegen
zsh scripts/resolve_local_packages.sh
xcodebuild -project HomeMediaCatalog.xcodeproj \
  -scheme HomeMediaCatalog \
  -destination 'generic/platform=iOS Simulator' \
  CODE_SIGNING_ALLOWED=NO build
```

**Important:** Local XcodeGen regeneration intentionally changes your working
copy's `.xcodeproj` to point at `Vendor/FFmpegApple`. Do **not commit**
that modified project; preserve the tracked **remote** package reference for
fresh checkouts and Xcode Cloud. Running `git restore
HomeMediaCatalog.xcodeproj/project.pbxproj` after building restores the tracked
configuration.

For Apple's process, see
[custom Xcode Cloud build scripts](https://developer.apple.com/documentation/xcode/writing-custom-build-scripts),
[dependencies in Xcode Cloud](https://developer.apple.com/documentation/xcode/making-dependencies-available-to-xcode-cloud)
and [GitHub access](https://developer.apple.com/documentation/xcode/connecting-xcode-cloud-to-github).
