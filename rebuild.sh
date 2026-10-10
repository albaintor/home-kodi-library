#!/bin/sh
# Run as the checkout owner. Only Docker commands use sudo when necessary.
set -eu

main() {
    repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
    cd "$repo_dir"

    for tool in git docker; do
        if ! command -v "$tool" >/dev/null 2>&1; then
            printf 'Commande manquante : %s\n' "$tool" >&2
            exit 1
        fi
    done
    if [ ! -f .env ]; then
        printf 'Créer et renseigner .env à partir de .env.example avant de continuer.\n' >&2
        exit 1
    fi
    if [ "$(git symbolic-ref --quiet --short HEAD)" != main ]; then
        printf 'Ce script met à jour main. Revenir sur cette branche avant de continuer.\n' >&2
        exit 1
    fi

    docker_with_sudo=0
    if ! docker info >/dev/null 2>&1; then
        if ! command -v sudo >/dev/null 2>&1; then
            printf 'Docker est inaccessible et sudo est indisponible.\n' >&2
            exit 1
        fi
        docker_with_sudo=1
        sudo -v
        docker_run info >/dev/null
    fi
    docker_run compose version >/dev/null

    # Let Compose read an explicit COMPOSE_FILE from .env, without sourcing it.
    # Otherwise retain the existing bridge + DNS configuration.
    if [ -z "${COMPOSE_FILE:-}" ] && ! grep -Eq '^[[:space:]]*(export[[:space:]]+)?COMPOSE_FILE[[:space:]]*=' .env; then
        COMPOSE_FILE=compose.yaml:compose.dns.yaml
        export COMPOSE_FILE
    fi

    printf '\nRécupération de la dernière version…\n'
    git pull --ff-only

    printf '\nConstruction de l’image…\n'
    docker_run build --network=host -t home-kodi-library:local .

    printf '\nRecréation du conteneur…\n'
    compose_run up -d --no-build --force-recreate library
    container_id=$(compose_run ps -a -q library)
    if [ -z "$container_id" ]; then
        printf 'Le conteneur library est introuvable.\n' >&2
        exit 1
    fi

    printf '\nVérification du démarrage (120 secondes maximum)…\n'
    attempt=0
    while [ "$attempt" -lt 60 ]; do
        status=$(docker_run inspect --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container_id")
        case "$status" in
            'running healthy')
                compose_run ps library
                printf '\nMise à jour terminée. Recharger le navigateur avec Ctrl+F5.\n'
                return 0
                ;;
            exited\ *|dead\ *|'running unhealthy'|'running none')
                break
                ;;
        esac
        attempt=$((attempt + 1))
        sleep 2
    done
    printf 'Le service n’a pas confirmé un démarrage sain. Derniers journaux :\n' >&2
    compose_run logs --tail=80 library
    exit 1
}

docker_run() {
    if [ "$docker_with_sudo" -eq 1 ]; then
        sudo docker "$@"
    else
        docker "$@"
    fi
}

compose_run() {
    # sudo may filter COMPOSE_FILE, so pass a shell override explicitly when set.
    if [ "$docker_with_sudo" -eq 1 ] && [ -n "${COMPOSE_FILE:-}" ]; then
        sudo env COMPOSE_FILE="$COMPOSE_FILE" docker compose "$@"
    else
        docker_run compose "$@"
    fi
}

# Parse the entire main function before git can update this script on disk.
main "$@"
