# Sources réseau et cloud

## Découverte locale

L'application iOS découvre les services SMB/NFS/FTP/SFTP par Bonjour sur le réseau local.
Le serveur propose \`GET /api/v1/playback/discovery\` pour détecter les annonces SSDP
UPnP sur **son propre réseau local** (durée limitée à 1,8 seconde, sans accéder aux
URL retournées). Sur Docker isolé en bridge ou réseau bloquant le multicast,
la découverte peut rester vide. Autoriser le réseau local dans les réglages iOS.

## Configuration d'une source

Les attributs protocolaires sont conservés dans la base SQLite côté serveur
(\`host\`, \`port\`, \`share\`, \`username\`, \`workgroup\`). Les mots de passe, quand
ils sont renseignés, sont chiffrés côté serveur avec Fernet. L'API ne renvoie
que \`has_password\`. L'application iOS ne conserve **aucun secret** en cache
hors connexion. Le mot de passe est conservé si une modification n'en saisit
pas de nouveau.

La configuration persistante n'est pas encore un navigateur de dossiers SMB,
NFS ou FTP. Les fichiers SMB peuvent également être sélectionnés via Fichiers
sur iOS, puis importés localement pour lecture sans serveur.

## Connexions OAuth

Fournisseurs : Google Drive, OneDrive, Dropbox, Box et pCloud.
iCloud Drive : **intégration native via Fichiers**, sans OAuth tiers.

Configurer sur le serveur ces variables d'environnement :

- \`CLOUD_OAUTH_REDIRECT_URL=https://votre-domaine/api/v1/playback/cloud/callback\`
  (HTTPS public accessible depuis le navigateur de l'iPhone, doit correspondre
  exactement aux URI autorisées des applications enregistrées chez les fournisseurs).
- \`CLOUD_OAUTH_CLIENTS\` : objet JSON dont chaque clé est un fournisseur,
  et chaque valeur contient \`client_id\` et, si nécessaire, \`client_secret\`.
  Exemple : \`{"google_drive":{"client_id":"...","client_secret":"..."},"dropbox":{"client_id":"..."}}\`.
- \`PLAYBACK_ENCRYPTION_KEY\` : clé Fernet URL-safe base64 facultative mais
  recommandée (\`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"\`).
  En son absence, clé dérivée d'API_TOKEN. Une rotation de l'API_TOKEN sans
  clé dédiée rend les credentials existants illisibles.

L'API administrateur \`POST /api/v1/playback/cloud/{location_id}/authorize\`
retourne une URL d'autorisation. Le navigateur suit le consentement, le serveur
échange le code OAuth et conserve **uniquement dans SQLite côté serveur** les
jetons chiffrés. \`GET /api/v1/playback/cloud/status\` ne divulgue aucun jeton,
et \`DELETE /api/v1/playback/cloud/{location_id}\` déconnecte localement.

La fenêtre de connexion expire après 10 minutes et ne fonctionne pas après
un redémarrage serveur avant retour du fournisseur. La déconnexion côté serveur
ne révoque pas les autorisations externes ; elles se gèrent chez le fournisseur.

**Périmètre actuel :** connexion OAuth, conservation chiffrée et renouvellement
automatique des jetons, navigation en lecture seule, récupération des vignettes
et streaming HTTP Range par ticket temporaire pour Google Drive, OneDrive,
Dropbox, Box et pCloud. Les fichiers cloud ne sont pas synchronisés localement.
Le fonctionnement réel doit être testé avec chaque compte/fournisseur, notamment
les redirections CDN, quotas et renouvellements des jetons.


## Modèle hybride retenu (iOS + web)

- Les adresses, ports, chemins de partage et noms sont des **descripteurs**
  centralisés sur le serveur, copiés dans l'application pour usage autonome.
  Lorsque l'API des emplacements n'est pas encore déployée (HTTP 404), la
  création iOS est conservée localement et mise en attente de synchronisation.
- Le **mot de passe saisi sur iOS** est conservé dans le trousseau de l'iPhone.
  Il n'est **pas** envoyé dans le descripteur synchronisé. Le serveur peut
  disposer d'un **autre secret**, enregistré depuis ses propres paramètres
  web dans sa base chiffrée. Ne pas confondre les deux.
- **iOS (voie recommandée)** : l'app lance le même OAuth **côté serveur**
  avec `POST /api/v1/playback/cloud/{id}/authorize?return_to=ios`. Le fournisseur
  revient sur l'URL HTTPS du serveur, qui échange et chiffre les jetons, puis
  renvoie l'iPhone sur `homekodilibrary://oauth/server` **sans jeton ni code**.
  iOS utilise les API authentifiées du serveur pour parcourir les dossiers,
  produire les vignettes et obtenir des tickets de streaming par plages HTTP.
  Un seul enregistrement d'application OAuth *web* suffit pour le navigateur
  et les clients iOS connectés au même serveur.
- **iOS (option autonome)** : la connexion directe à Microsoft Graph ou aux
  autres fournisseurs reste possible si des clients OAuth publics natifs sont
  explicitement enregistrés, et insérés dans `OAuth.local.xcconfig` au build.
  Sans client natif, le bouton de connexion directe n'est pas présenté.
- **Fichiers iOS (repli)** : les extensions File Provider d'OneDrive, Dropbox,
  Google Drive, etc., utilisent leur propre authentification. Un média choisi
  depuis Fichiers peut être importé pour lecture **hors ligne**, sans identifiant
  OAuth propre à Home Kodi Library. Ce n'est pas du streaming direct.
- **Configuration obligatoire (une seule fois)** : pour OneDrive en mode
  serveur, enregistrer une application Microsoft Entra autorisant les comptes
  Microsoft personnels, choisir la plateforme **Web**, déclarer exactement
  `https://VOTRE-DOMAINE/api/v1/playback/cloud/callback`, créer un
  `client_secret` côté serveur et enregistrer l'ID/secret dans Paramètres →
  Services cloud → Configuration développeur. L'URL HTTPS doit être réellement
  joignable depuis l'iPhone. Aucun secret ne doit être incorporé à l'application
  iOS. Les fournisseurs tiers ont leurs propres consoles OAuth.

**Limite actuelle :** la présence d'un emplacement enregistré ne signifie
pas qu'il peut être parcouru. Les adaptateurs SMB, FTP, NFS, UPnP et les APIs
cloud de navigation/streaming restent distincts des écrans de configuration.
