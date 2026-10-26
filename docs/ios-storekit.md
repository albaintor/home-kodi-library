# Video player: 10 free sessions, then a €2 lifetime unlock

Implemented in `main` (subject to App Store Connect and release validation).

## Business model

- Catalogue, AI search and **Kodi remote playback on the Kodi device** remain free.
- **On-device video playback**, regardless of source (Files, share extension,
  SMB, local downloads, NAS, cloud, HTTP streaming, Kodi VFS), includes exactly
  **10 free full-screen playback sessions** on a device.
- From the **11th launch**, a StoreKit 2 purchase sheet offers **one lifetime
  non-consumable unlock** covering the entire local video player and all MEMC
  modes. No subscription, no separate MEMC purchase.
- In trial mode, the player OSD displays `Evaluation - x / 10 trials remaining`
  beneath the title (localized to French); it hides with the controls and
  is absent after unlocking.
- Playback sessions, not file names, are counted: replaying the same movie in a
  newly opened player counts again. Seek, picture-in-picture, re-rendering and
  MEMC settings during the same session do **not** consume additional trials.
- The count is persisted via UserDefaults plus a Keychain backup for ordinary
  reinstalls. This remains a **device-local trial**, not an account-wide
  counter; strict cross-device free-trial enforcement requires a back end.
- Purchase verification is through **verified** StoreKit 2 transactions and
  `Transaction.currentEntitlements`, with `Transaction.updates` for changes,
  refunds and other-device purchases. Unverified/revoked entitlements do not
  unlock paid access.
- `Restore purchases` is available in the purchase screen and in Settings.
  `AppStore.sync()` is triggered only by an explicit restore tap.

## Required App Store Connect configuration

**This cannot be created by Xcode code alone.** In App Store Connect, open the
app matching the actual `fr.albaintor.HomeKodiLibrary` bundle identifier
(the display name is Home Media Catalog). Under **Monetization → In-App
Purchases**, create:

| Field | Value |
|---|---|
| Reference name | Home Media Catalog — Video Player |
| Product ID (immutable) | `fr.albaintor.HomeKodiLibrary.video_player_unlock` |
| Type | **Non-Consumable** |
| French storefront price | **€2.00**, if this price point is offered |
| Duration | Permanent, no subscription or recurring charge |
| English name | Unlock video player |
| French name | Débloquer le lecteur vidéo |

Choose the closest intended €2 price point if Apple does not expose exactly
€2.00. Apple handles storefront-specific prices and taxes. The application
uses `Product.displayPrice` rather than hardcoding a fake price.

Provide the French and English product descriptions, screenshot for review,
the applicable tax/banking agreements and the IAP's required App Store review
information. The product must be made available for the bundle identifier and
be ready for sandbox / TestFlight validation. **Until App Store Connect has
this product, `Product.products(for:)` can return an empty list: the buy
button remains disabled after the ten trial sessions.**

There is no Apple Pay merchant entitlement for this StoreKit In-App Purchase.
Do not enable Apple Pay as a substitute.

## Developer tests

1. Use an App Store sandbox tester and run on a physical iPhone or with a
   matching local StoreKit configuration.
2. Open any supported on-device video ten times (including reopening the
   same file): each launch counts once. On attempt 11 the player is **not**
   started, and the purchase screen appears instead.
3. Confirm dismissing the purchase screen **does not** decrement another
   trial; relaunch stays locked at 0 remaining.
4. Purchase the lifetime entitlement in the sandbox; verify video launches
   again immediately, including MEMC, and the counter does not increase.
5. Relaunch the app, reinstall on the same or a second device, and verify
   the purchased entitlement is restored from StoreKit.
6. From Settings, test explicit Restore Purchases and cancellation/pending
   purchase scenarios. Verify a refunded/revoked transaction does not grant
   unlimited access.
7. Confirm catalog and Kodi remote controls remain accessible when video is
   locked. Check SMB, iCloud Files, share extension, Kodi local streaming,
   HTTP/network/cloud and downloaded video are all gated.
8. Run the standalone Swift trial boundary test and normal regression suite:

```bash
swiftc HomeMediaCatalog/Playback/VideoPlayerTrialPolicy.swift \
    tests/video_player_trial/main.swift -o /tmp/video-player-trials
/tmp/video-player-trials
python3 -m pytest -q tests/test_video_player_monetization.py
```

### Packaging with current dynamic FFmpeg variant

```bash
git switch main
git pull --ff-only
zsh scripts/resolve_local_packages.sh
```

Xcode Cloud runs the equivalent setup from `ci_scripts/ci_post_clone.sh`;
see [public Xcode Cloud instructions](ios-xcode-cloud.md). The committed Xcode project refers to the **local** PrismCore package.
The post-clone/local preparation downloads the public adapter and creates
the ignored `Vendor/FFmpegApple` directory before compilation.
The embedded AMSMB2 framework must stay present and signed; see
`scripts/audit_dyld_launch.py`.

## Shipping blockers not solved by the purchase gate

- Physical-device StoreKit sandbox / TestFlight payment acceptance and
  availability of the product in App Store Connect.
- Full dynamic-FFmpeg bundle, app signing and source/notices review for the
  shipped archive, plus an end-to-end regression pass for playback/MEMC.
- Source/licensing audit of transitive libmpv/PrismCore/other LGPL or GPL
  dependencies. Linking FFmpeg dynamically is not alone legal certification.
- StoreKit price and in-app purchase metadata must be approved by Apple before
  the paid unlock can be sold. No sales or approvals were performed here.

## Reset evaluation attempts on an iPhone installed via Xcode

For Xcode **Debug** builds, open **Settings → Video player access →
Reset evaluation trials** and confirm. This deletes the local
`video.player.freePlaysUsed.v1` UserDefaults key **and** the Keychain item
identified by `fr.albaintor.HomeKodiLibrary.videoPlayerTrial` /
`freePlaysUsed.v1`, then updates the displayed remaining attempts to 10.
A StoreKit purchase stays active; the reset only affects evaluation usage.

The button is omitted at compile time from **Release**, TestFlight and
App Store builds. Uninstalling/reinstalling the app does not reliably
reset evaluation usage because Keychain items can survive reinstalls.
