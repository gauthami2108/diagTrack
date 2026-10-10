# Shared by the other scripts: always work from the DiagTrack folder.
cd "$(dirname "$0")/.." || exit 1
if ! docker compose version >/dev/null 2>&1; then
    echo "Docker is not installed or not running. See docs/01-INSTALL.md, step 2."
    exit 1
fi

# Stops with clear instructions when setup has not been run in this folder yet (also after upgrading from the
# first version, which kept its passwords in .env instead of the secrets folder).
require_setup() {
    if [ ! -s secrets/db_password ]; then
        echo
        # The database folder belongs to the database's own user, so it may not be readable from here.
        if [ -d data/postgres ] && { [ ! -r data/postgres ] || [ -n "$(ls -A data/postgres 2>/dev/null)" ]; }; then
            echo "This folder holds a DiagTrack made by an earlier version. Run setup once to upgrade it;"
            echo "every ticket, user and attachment is kept:"
        else
            echo "DiagTrack is not set up in this folder yet. Run setup first:"
        fi
        echo
        echo "    docker compose run --rm setup"
        echo
        echo "Then start it again with:  ./scripts/start.sh"
        exit 1
    fi
}
