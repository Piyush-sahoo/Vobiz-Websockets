"""
make_call.py — Trigger an Outbound Call via Vobiz REST API
============================================================
Auto-discovers the ngrok answer URL from the running agent/server.py.
Run this AFTER server.py is up and ngrok is established.

Usage:
    python make_call.py                        # uses TO_NUMBER / FROM_NUMBER from .env
    python make_call.py --to +91XXXXXXXXXX     # override destination
    python make_call.py --answer-url https://  # skip auto-discovery
"""

import os
import sys
import argparse
import requests
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
VOBIZ_AUTH_ID    = os.getenv("VOBIZ_AUTH_ID")
VOBIZ_AUTH_TOKEN = os.getenv("VOBIZ_AUTH_TOKEN")
VOBIZ_API_BASE   = "https://api.vobiz.ai/api/v1"

FROM_NUMBER = os.getenv("FROM_NUMBER")
TO_NUMBER   = os.getenv("TO_NUMBER")

# Port where agent/server.py is running (used for auto-discovery)
HTTP_PORT = int(os.getenv("HTTP_PORT", "8001"))


def make_call(to_number: str, from_number: str, answer_url: str):
    """Make an outbound call using the Vobiz REST API."""
    if not VOBIZ_AUTH_ID or not VOBIZ_AUTH_TOKEN:
        print("Error: VOBIZ_AUTH_ID and VOBIZ_AUTH_TOKEN must be set in .env")
        sys.exit(1)

    url = f"{VOBIZ_API_BASE}/Account/{VOBIZ_AUTH_ID}/Call/"

    headers = {
        "Content-Type": "application/json",
        "X-Auth-ID":    VOBIZ_AUTH_ID,
        "X-Auth-Token": VOBIZ_AUTH_TOKEN,
    }

    payload = {
        "from":          from_number,
        "to":            to_number,
        "answer_url":    answer_url,
        "answer_method": "POST",
        "hangup_url":    answer_url.replace("/answer", "/hangup"),
        "hangup_method": "POST",
    }

    print(f"Making call...")
    print(f"  From:       {from_number}")
    print(f"  To:         {to_number}")
    print(f"  Answer URL: {answer_url}")
    print()

    try:
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()

        call_uuid = data.get("request_uuid", data.get("call_uuid", "unknown"))
        print(f"Call initiated!")
        print(f"  Call UUID: {call_uuid}")
        return data

    except requests.exceptions.HTTPError as e:
        print(f"HTTP Error: {e}")
        print(f"  Response: {e.response.text if e.response else 'No response'}")
        sys.exit(1)
    except requests.exceptions.ConnectionError:
        print("Connection error. Check your internet connection.")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Make an outbound call via Vobiz")
    parser.add_argument("--to",         type=str, default=TO_NUMBER,   help="Destination number e.g. +919876543210")
    parser.add_argument("--from",       dest="from_number", type=str, default=FROM_NUMBER, help="Your Vobiz number")
    parser.add_argument("--answer-url", type=str, default=None,        help="Answer URL (auto-detected if omitted)")

    args = parser.parse_args()

    to_number   = args.to
    from_number = args.from_number
    answer_url  = args.answer_url

    if not to_number:
        print("Error: --to number is required (or set TO_NUMBER in .env)")
        sys.exit(1)

    if not from_number:
        print("Error: --from number is required (or set FROM_NUMBER in .env)")
        sys.exit(1)

    if not answer_url:
        # Auto-discover from the running server.py health endpoint
        health_url = f"http://127.0.0.1:{HTTP_PORT}/health"
        try:
            health    = requests.get(health_url, timeout=3)
            data      = health.json()
            ngrok_url = data.get("ngrok_url")
            if ngrok_url:
                answer_url = f"{ngrok_url}/answer"
                print(f"Auto-detected Answer URL: {answer_url}")
            else:
                print(f"Error: Could not read ngrok_url from {health_url}")
                print("Is server.py running?")
                sys.exit(1)
        except Exception:
            print(f"Error: Could not connect to server.py at {health_url}")
            print("Start server.py first, or pass --answer-url manually.")
            sys.exit(1)

    make_call(to_number, from_number, answer_url)


if __name__ == "__main__":
    main()
