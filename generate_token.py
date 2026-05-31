"""One-time local OAuth flow that produces token.json for Google Sheets access.

Run once on your own machine:

    python generate_token.py

A browser window opens; log in with the Google account that can EDIT the target
spreadsheet. This writes ``token.json`` next to this script. Then base64-encode
it for the ``GOOGLE_OAUTH_TOKEN_B64`` GitHub secret (see README.md).

Requires ``client_secret.json`` (your OAuth client, "Desktop app" type) in this
directory — download it from the Google Cloud Console.
"""
from __future__ import annotations

from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

# Must match the scope used by the bot (sheets.py).
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

HERE = Path(__file__).resolve().parent
CLIENT_SECRET = HERE / "client_secret.json"
TOKEN_FILE = HERE / "token.json"


def main() -> None:
    if not CLIENT_SECRET.exists():
        raise SystemExit(
            "client_secret.json not found.\n"
            "Download your OAuth client (Desktop app) from the Google Cloud "
            "Console and save it as client_secret.json next to this script."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
    creds = flow.run_local_server(port=0)
    TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")

    print(f"✅ Wrote {TOKEN_FILE}")
    print("Next steps:")
    print("  1. macOS:  base64 -i token.json | pbcopy")
    print("     Linux:  base64 -w0 token.json")
    print("  2. Paste the output into the GOOGLE_OAUTH_TOKEN_B64 GitHub secret.")


if __name__ == "__main__":
    main()
