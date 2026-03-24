#!/usr/bin/env python3
"""
One-time setup script: generates Gmail OAuth2 refresh token.
Run locally — NOT in GitHub Actions.

Usage:
  python setup_gmail_oauth.py
  python setup_gmail_oauth.py --credentials path/to/credentials.json
"""

import argparse, json, sys

def main():
    parser = argparse.ArgumentParser(description="Generate Gmail OAuth2 refresh token")
    parser.add_argument("--credentials", help="Path to Google OAuth2 credentials JSON file")
    args = parser.parse_args()

    if args.credentials:
        with open(args.credentials) as f:
            creds = json.load(f)
        # Handle both "installed" and "web" credential types
        key = "installed" if "installed" in creds else "web"
        client_id = creds[key]["client_id"]
        client_secret = creds[key]["client_secret"]
        print(f"Loaded credentials from {args.credentials}")
    else:
        print("No credentials file provided. Enter manually:\n")
        client_id = input("Client ID: ").strip()
        client_secret = input("Client Secret: ").strip()

    if not client_id or not client_secret:
        print("ERROR: client_id and client_secret are required")
        sys.exit(1)

    # Import here so the script gives a helpful error if not installed
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("\nERROR: Required package missing. Install with:")
        print("  pip install google-auth-oauthlib google-api-python-client")
        sys.exit(1)

    SCOPES = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.labels",
    ]

    # Build flow from client config
    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        },
        scopes=SCOPES,
    )

    print("\n" + "=" * 50)
    print("A browser window will open.")
    print("Log in as: home@edisonhaus.com")
    print("Grant all requested permissions.")
    print("=" * 50 + "\n")

    creds = flow.run_local_server(port=8080, prompt="consent", access_type="offline")

    print("\n" + "=" * 60)
    print("SUCCESS! Add these as GitHub Actions secrets:")
    print("=" * 60)
    print(f"\nGMAIL_CLIENT_ID={client_id}")
    print(f"GMAIL_CLIENT_SECRET={client_secret}")
    print(f"GMAIL_REFRESH_TOKEN={creds.refresh_token}")
    print(f"\nAlso ensure these secrets exist:")
    print(f"GMAIL_SENDER=home@edisonhaus.com")
    print(f"GMAIL_TO=nicholas.jacksondesign@gmail.com")
    print(f"GMAIL_APP_PASSWORD=<your Gmail App Password>")
    print(f"\nAdd secrets at:")
    print(f"https://github.com/ViciousStudio-tech/edisonhaus-automation/settings/secrets/actions")
    print(f"\n{'=' * 60}")
    print("To create a Gmail App Password:")
    print("1. Go to https://myaccount.google.com/security")
    print("2. Under '2-Step Verification' (must be enabled), click 'App passwords'")
    print("3. Select app: 'Other' → name it 'EdisonHaus Automation'")
    print("4. Copy the 16-character password → add as GMAIL_APP_PASSWORD secret")
    print("=" * 60)


if __name__ == "__main__":
    main()
