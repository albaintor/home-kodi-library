# Accès depuis Home Assistant

L'interface dédiée fournit recherche libre, filtres combinables, tris et fiches avec éditions. Les cartes Kodi/media-player standard pilotent surtout la lecture. Kodi Search couvre certains usages de bibliothèque, sans constituer à lui seul cette interface multi-source. JellyHA dépend de Jellyfin ; les cartes Arr dépendent de Radarr/Sonarr. La solution retenue est donc un service Docker autonome et une vue Ingress.

## Serveur Docker accessible depuis Home Assistant

Le composant communautaire [hass_ingress](https://github.com/lovelylain/hass_ingress) fait le relais entre Home Assistant et un service externe. Il ne s'agit pas de l'Ingress Supervisor d'un add-on officiel.

1. Installer `lovelylain/hass_ingress` selon son README (dépôt personnalisé HACS, catégorie intégration).
2. Générer un secret aléatoire pour `INGRESS_TOKEN` dans `.env`, distinct de `API_TOKEN` ; redémarrer le conteneur si cette valeur change.
3. Déclarer la vue dans `configuration.yaml` et la même valeur dans `secrets.yaml`.

```yaml
ingress:
  kodi_library:
    work_mode: ingress
    title: Bibliothèque
    icon: mdi:movie-open
    url: http://HOTE_DOCKER:8099
    headers:
      X-Kodi-Library-Ingress-Token: !secret kodi_library_ingress_token
```

```yaml
# secrets.yaml
kodi_library_ingress_token: "SECRET_ALEATOIRE_IDENTIQUE_A_INGRESS_TOKEN"
```

4. Redémarrer Home Assistant. La vue apparaît dans sa barre latérale et s'authentifie grâce au secret transmis côté serveur. Vérifier l'accès depuis le réseau mobile.

Home Assistant doit pouvoir joindre `HOTE_DOCKER:8099`. Les fichiers statiques et appels API sont relatifs au chemin du service pour fonctionner derrière un préfixe Ingress. Le navigateur ne reçoit pas le secret Ingress. Les utilisateurs autorisés à ouvrir cette vue disposent des droits d'administration du catalogue : limiter l'accès à la vue à des personnes de confiance selon la configuration de hass_ingress.

Une iframe pointant directement vers `http://HOTE_DOCKER:8099` dépend de l'accès du navigateur au réseau privé et peut être bloquée depuis Home Assistant en HTTPS. Ingress donne au navigateur une URL sur le même domaine Home Assistant, puis Home Assistant contacte le serveur Docker. C'est cette configuration qu'il faut privilégier pour un accès distant.

Pour un accès exclusivement HTTPS, `COOKIE_SECURE=true` renforce les sessions web directes. L'authentification Ingress fonctionne par en-tête et ne dépend pas de ce cookie. Ne pas configurer un proxy public qui ajoute le secret Ingress pour tous les visiteurs.

## Application iOS et autres clients

Une application iOS utilise directement l'API du service Docker avec un jeton Bearer, via VPN ou reverse proxy HTTPS. L'accès Home Assistant et l'API native sont deux portes d'entrée du même serveur et utilisent le même catalogue SQLite. Ne pas stocker le jeton Ingress dans l'application iOS.

## Sources vérifiées pour le choix

- [Carte Webpage HA](https://www.home-assistant.io/dashboards/iframe/)
- [Intégration Kodi HA](https://www.home-assistant.io/integrations/kodi/)
- [Ingress Supervisor](https://developers.home-assistant.io/docs/add-ons/presentation/#ingress)
- [hass_ingress, configuration et exemples](https://github.com/lovelylain/hass_ingress)
- [Kodi Search Card](https://github.com/jtbgroup/kodi-search-card)

La compatibilité avec le chemin de proxy est vérifiable localement. La configuration réelle de Home Assistant, ses droits d'accès et le réseau du serveur Docker restent à valider sur l'installation cible.
