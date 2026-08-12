# Deploying the service to Fly.io

This covers standing up Layer 1 (the FastAPI service in `src/service.py`) as
an always-on hosted endpoint. Nothing here places, sizes, or authorizes a
wager — see README.md "Shadow mode".

## What's here

- `Dockerfile` — builds a slim Python 3.11 image, runs as a non-root user,
  listens on 8080.
- `docker/entrypoint.sh` — at container start, writes
  `GOOGLE_APPLICATION_CREDENTIALS_JSON` (a Fly secret holding the full
  service-account key content) to disk and points
  `GOOGLE_APPLICATION_CREDENTIALS` at it, since Fly secrets are env vars, not
  mounted files. Also seeds `config/config.yaml` from the example if missing
  (not currently read by the app — gate thresholds are Python constants — but
  harmless and forward-compatible).
- `fly.toml` — app config: internal port 8080, `/healthz` health check,
  scale-to-zero (`min_machines_running = 0`) since this only needs to be up
  when a scheduled trigger calls it, `shared-cpu-1x` / 256mb (this workload is
  light — pure request/response, no background jobs).
- `.dockerignore` — keeps tests, docs, `.env`, and any real credentials file
  out of the built image.

## One-time setup

```bash
# from the repo root, on your machine (needs your Fly account)
fly auth login
fly launch --no-deploy --copy-config --name <pick-a-globally-unique-name>
```

`fly launch` will detect `fly.toml` and offer to reuse it — say yes to
`--copy-config`, and pick a name (Fly app names are global, `hard-rock-bets`
itself may already be taken).

## Secrets

None of these are in git. Set them once per Fly app:

```bash
fly secrets set \
  OWLS_INSIGHT_API_KEY="..." \
  GOOGLE_SHEETS_SPREADSHEET_ID="..." \
  GOOGLE_SHEETS_DECISION_LOG_TAB="Decision Log" \
  GOOGLE_APPLICATION_CREDENTIALS_JSON="$(cat /path/to/service-account.json)" \
  BANKROLL_USD="..."
```

Reminder from README: the service-account email inside that JSON key needs
edit access on the actual tracker spreadsheet, or Sheets writes 403.

## Deploy

```bash
fly deploy
```

Then confirm:

```bash
curl https://<your-app-name>.fly.dev/healthz
# {"status":"ok","version":"1.0.0","shadow_mode":true,
#  "odds_api_key_configured":true,"sheets_configured":true}
```

If either `_configured` flag is `false`, the matching secret above didn't
land — check `fly secrets list` and `fly logs`.

## After that

Point the trigger prompt templates (`prompts/morning.md`,
`prompts/afternoon.md`, `prompts/late.md`) at
`https://<your-app-name>.fly.dev` in place of `{{SERVICE_URL}}` — that's the
next step (cutting the scheduled triggers over), not part of this one.

## Local dev / sanity check without Docker

```bash
pip install -r requirements.txt
uvicorn src.service:app --reload --port 8000
curl localhost:8000/healthz
```
