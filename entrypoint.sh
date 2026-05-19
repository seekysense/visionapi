#!/bin/sh
set -e

# Fix ownership of volume-mounted YAML files so appuser can write them via the API.
# Runs as root (entrypoint starts before USER drop), then hands off to appuser.
chown appuser:appuser \
    /app/cameras.yaml \
    /app/actions.yaml \
    /app/sequences.yaml \
    2>/dev/null || true

exec gosu appuser "$@"
