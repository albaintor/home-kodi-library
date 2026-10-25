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
