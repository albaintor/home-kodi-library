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

**Périmètre actuel :** autorisation et enregistrement des comptes, pas encore
exploration des répertoires cloud, renouvellement des jetons expirés, lecture
directe cloud ou synchronisation de fichiers. Ces adaptateurs seront nécessaires
pour offrir les fonctions de navigation et streaming de VLC.


## Modèle hybride retenu (iOS + web)

- Les adresses, ports, chemins de partage et noms sont des **descripteurs**
  centralisés sur le serveur, copiés dans l'application pour usage autonome.
  Lorsque l'API des emplacements n'est pas encore déployée (HTTP 404), la
  création iOS est conservée localement et mise en attente de synchronisation.
- Le **mot de passe saisi sur iOS** est conservé dans le trousseau de l'iPhone.
  Il n'est **pas** envoyé dans le descripteur synchronisé. Le serveur peut
  disposer d'un **autre secret**, enregistré depuis ses propres paramètres
  web dans sa base chiffrée. Ne pas confondre les deux.
- Pour iOS, la première intégration cloud passe par les **extensions File Provider**
  des applications tierces présentes dans Fichiers. Leur authentification se
  fait directement avec le fournisseur et n'utilise aucune route OAuth du
  serveur ; l'utilisateur peut importer un média, puis le lire hors ligne.
- Une future navigation cloud directe depuis l'app nécessitera l'enregistrement
  d'un **client OAuth iOS** spécifique à chaque fournisseur, l'échange
  authorization code + PKCE et la conservation du token dans Keychain.
  Le client OAuth web et ses jetons restent indépendants.
- Pour le web, la section Paramètres / Services cloud utilise les API OAuth
  du serveur. Le serveur conserve les jetons du navigateur web uniquement.
  Les autorisations web ne sont jamais renvoyées à iOS.

**Limite actuelle :** la présence d'un emplacement enregistré ne signifie
pas qu'il peut être parcouru. Les adaptateurs SMB, FTP, NFS, UPnP et les APIs
cloud de navigation/streaming restent distincts des écrans de configuration.
