# Lecture — configuration des emplacements

L'API `/api/v1/playback/locations` stocke dans SQLite, sur le serveur, les descripteurs de sources (`id`, `name`, `kind`, `target`). Lecture : client authentifié ; création, modification, suppression : administrateur. Le client iOS conserve une copie des seuls descripteurs par URL de serveur pour pouvoir afficher l'onglet Lecture sans réseau. Les secrets ne figurent pas dans cette synchronisation.

## Prise en charge actuelle

- Fichiers déjà téléchargés ou importés sur iOS : lus par le lecteur AVPlayer/PrismCore, sans serveur.
- Fichiers obtenus depuis l'app Fichiers et ses fournisseurs (iCloud Drive, OneDrive, Google Drive, Dropbox, SMB si l'utilisateur s'y connecte dans Fichiers) : importés dans le conteneur de l'app pour lecture hors ligne. Les fournisseurs doivent être configurés séparément dans Fichiers.
- URL HTTP ou HTTPS de fichier multimédia : lecture en streaming sur iOS lorsque joignable.
- Descripteurs FTP et NFS : sauvegardés et synchronisés, mais **ni navigation ni passerelle serveur** actuellement. Ils nécessiteront un adaptateur dédié côté serveur (ou un montage NFS + un serveur HTTP sécurisé). Les chemins SMB ne sont pas montés ou accessibles automatiquement depuis le serveur.
- Les identifiants NAS et autorisations OAuth des clouds ne sont pas gérés par cette API. Ne pas insérer d'identifiants dans `target`.

**Limite importante :** cette première version n'implémente pas un explorateur réseau multi-protocole ni un moteur OAuth partagé entre iOS et le serveur. Elle prépare les emplacements centralisés, l'onglet Lecture et l'accès hors ligne. Les fichiers importés sont copiés (consommation d'espace de stockage iOS).

Toute future passerelle de fichiers devra valider les chemins, appliquer une liste de sources autorisées, protéger les requêtes Range et utiliser des tickets de lecture temporaires sans divulguer les secrets des partages.


## Nettoyage de la médiathèque Kodi

La commande `POST /api/v1/sources/{source_id}/clean` (administrateur requis)
déclenche `VideoLibrary.Clean` par JSON-RPC WebSocket sur l'instance
associée à la source Kodi. Le paramètre optionnel `player_id` sélectionne
une instance précise. L'action est distincte de `/scan` et de `/sync`.

Le serveur attend `VideoLibrary.OnCleanStarted` puis
`VideoLibrary.OnCleanFinished` avant de synchroniser à nouveau le
catalogue. Si la connexion WebSocket ou la confirmation échoue, le
catalogue local reste inchangé ; aucun repli SQL n'est présenté comme
un nettoyage réussi. Pour les bibliothèques Kodi multi-éditions, la
synchronisation utilise MariaDB si configurée, afin de ne pas écraser les
informations de versions absentes du JSON-RPC.

**Attention :** vérifier l'accessibilité de tous les montages SMB/NFS et
disques avant le nettoyage, sinon Kodi risque de retirer de sa médiathèque
des entrées toujours valides. L'action ne supprime pas les fichiers vidéo
physiques. Progression et erreurs : `GET /api/v1/jobs/{job_id}`.
