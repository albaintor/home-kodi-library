# Access from Home Assistant

The dedicated interface provides free-text search, combinable filters, sorting, and detailed views with editing capabilities. Standard Kodi/media-player cards primarily control playback. Kodi Search covers some library use cases but does not, on its own, provide this multi-source interface. JellyHA depends on Jellyfin, while Arr cards depend on Radarr/Sonarr. The chosen solution is therefore a standalone Docker service with an Ingress view.

## Docker server accessible from Home Assistant

The community integration [hass_ingress](https://github.com/lovelylain/hass_ingress) acts as a proxy between Home Assistant and an external service. This is not the Supervisor Ingress mechanism used by an official add-on.

1. Install `lovelylain/hass_ingress` according to its README (custom HACS repository, Integration category).
2. Generate a random secret for `INGRESS_TOKEN` in `.env`, separate from `API_TOKEN`; restart the container if this value changes.
3. Define the view in `configuration.yaml` and set the same secret value in `secrets.yaml`.

```yaml
ingress:
  kodi_library:
    work_mode: ingress
    title: Library
    icon: mdi:movie-open
    url: http://DOCKER_HOST:8099
    headers:
      X-Kodi-Library-Ingress-Token: !secret kodi_library_ingress_token
```

```yaml
# secrets.yaml
kodi_library_ingress_token: "SAME_RANDOM_SECRET_AS_INGRESS_TOKEN"
```

4. Restart Home Assistant. The view appears in the sidebar and authenticates using the secret forwarded server-side. Verify that it can be accessed over a mobile network.

Home Assistant must be able to reach `DOCKER_HOST:8099`. Static files and API requests use paths relative to the service so they work behind an Ingress path prefix. The browser never receives the Ingress secret. Users allowed to open this view have catalog administration privileges: restrict access to trusted users through the hass_ingress configuration.

An iframe pointing directly to `http://DOCKER_HOST:8099` requires the browser to have access to the private network and may be blocked when Home Assistant is accessed over HTTPS. With Ingress, the browser uses a URL on the same domain as Home Assistant, which then connects to the Docker server. This is the preferred configuration for remote access.

For HTTPS-only access, `COOKIE_SECURE=true` strengthens direct web sessions. Ingress authentication uses an HTTP header and does not depend on this cookie. Do not configure a public proxy that adds the Ingress secret for every visitor.

## iOS app and other clients

An iOS app accesses the Docker service API directly using a Bearer token, through a VPN or HTTPS reverse proxy. Home Assistant access and the native API are two entry points to the same server and share the same SQLite catalog. Do not store the Ingress token in the iOS app.

## References supporting this approach

- [Home Assistant Webpage card](https://www.home-assistant.io/dashboards/iframe/)
- [Home Assistant Kodi integration](https://www.home-assistant.io/integrations/kodi/)
- [Supervisor Ingress](https://developers.home-assistant.io/docs/add-ons/presentation/#ingress)
- [hass_ingress configuration and examples](https://github.com/lovelylain/hass_ingress)
- [Kodi Search Card](https://github.com/jtbgroup/kodi-search-card)

Compatibility with the proxy path can be verified locally. The actual Home Assistant configuration, access permissions, and Docker server network connectivity still need to be validated in the target deployment.
