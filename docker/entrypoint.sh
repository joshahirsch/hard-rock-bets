#!/bin/sh
# Container entrypoint for the hard-rock-bets service.
#
# Fly.io secrets are plain env vars — there's nowhere to mount a file
# directly, so a service-account JSON key has to arrive as one env var
# (GOOGLE_APPLICATION_CREDENTIALS_JSON, set via `fly secrets set`) and get
# written to disk here before the app starts. If that var isn't set, we fall
# back to whatever GOOGLE_APPLICATION_CREDENTIALS already points at (e.g. a
# local bind-mount in dev), and Sheets writes just report unconfigured.
set -eu

if [ -n "${GOOGLE_APPLICATION_CREDENTIALS_JSON:-}" ]; then
    CRED_PATH="/app/gcp-credentials.json"
    printf '%s' "$GOOGLE_APPLICATION_CREDENTIALS_JSON" > "$CRED_PATH"
    export GOOGLE_APPLICATION_CREDENTIALS="$CRED_PATH"
fi

if [ ! -f "${HRB_CONFIG_PATH:-config/config.yaml}" ] && [ -f config/config.example.yaml ]; then
    mkdir -p "$(dirname "${HRB_CONFIG_PATH:-config/config.yaml}")"
    cp config/config.example.yaml "${HRB_CONFIG_PATH:-config/config.yaml}"
fi

exec "$@"
