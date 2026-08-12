#!/usr/bin/env python3
"""One-time helper: mint a long-lived OAuth refresh token for Sheets access.

Run this LOCALLY, on a machine with a browser, once. It never runs as part
of the deployed service -- the service only ever uses the refresh token this
produces (via GOOGLE_OAUTH_CLIENT_ID / _SECRET / _REFRESH_TOKEN), never the
interactive flow itself.

Why this exists: this project's normal path is a service-account JSON key
(see README.md). Some Google Cloud orgs enforce the
`iam.disableServiceAccountKeyCreation` policy with no project-level override
available to a non-org-admin account -- if that's you, use this instead.

Prerequisites (one-time, in Google Cloud Console, on the SAME project as
your Sheets access):
  1. APIs & Services > Credentials > Create Credentials > OAuth client ID.
  2. Application type: Desktop app. Name: anything (e.g. "hard-rock-bets CLI").
  3. Download the client secret JSON -- the "Download OAuth client" button
     next to your new client ID.
  4. If prompted to configure an OAuth consent screen first: User type
     "External" is fine for personal use; add yourself as a test user; the
     app doesn't need to be published/verified for this use case.
  5. Share the tracker spreadsheet with the SAME Google account you'll sign
     in with in step 2 of this script (Editor access), if it isn't already.

Usage:
    pip install google-auth-oauthlib   # already in requirements.txt
    python scripts/get_oauth_refresh_token.py /path/to/client_secret.json

This opens a browser for you to sign in and consent, then prints the three
values to set as Fly secrets. Nothing here is written to disk except what
you paste yourself.
"""

from __future__ import annotations

import sys

SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} /path/to/client_secret.json", file=sys.stderr)
        return 2

    client_secret_path = sys.argv[1]

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print(
            "google-auth-oauthlib is required: pip install google-auth-oauthlib",
            file=sys.stderr,
        )
        return 1

    flow = InstalledAppFlow.from_client_secrets_file(
        client_secret_path, scopes=[SHEETS_SCOPE]
    )
    # Forces a refresh token even on a re-consent; access_type=offline is the
    # part that actually matters (a refresh token with no expiry).
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

    if not creds.refresh_token:
        print(
            "\nNo refresh token was returned. This usually means you've "
            "already granted this app consent before and Google didn't "
            "re-issue one. Fix: go to https://myaccount.google.com/permissions "
            ", remove access for this app's name, and re-run this script.",
            file=sys.stderr,
        )
        return 1

    print("\nSuccess. Set these three as Fly secrets (fly secrets set ...):\n")
    print(f"GOOGLE_OAUTH_CLIENT_ID={creds.client_id}")
    print(f"GOOGLE_OAUTH_CLIENT_SECRET={creds.client_secret}")
    print(f"GOOGLE_OAUTH_REFRESH_TOKEN={creds.refresh_token}")
    print(
        "\nDo not commit these anywhere. The refresh token does not expire "
        "on its own -- treat it like a password. Revoke it any time at "
        "https://myaccount.google.com/permissions if needed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
