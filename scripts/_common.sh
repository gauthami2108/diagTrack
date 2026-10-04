# Shared by the other scripts: always work from the DiagTrack folder.
cd "$(dirname "$0")/.." || exit 1
if ! docker compose version >/dev/null 2>&1; then
    echo "Docker is not installed or not running. See docs/01-INSTALL.md, step 2."
    exit 1
fi
