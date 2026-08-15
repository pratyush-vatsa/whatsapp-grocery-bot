"""
One-time (well, every ~5 days) login for the ONE Swiggy account your
whole family bot will order through.

Run this directly whenever you need to (re)login:
    python swiggy_login.py

It opens your browser -> you log in with phone number + OTP for the
account that should receive every order -> the resulting access token
gets saved to swiggy_token.json for main.py to reuse. No refresh token
exists yet in Swiggy's OAuth, so when main.py starts refusing to search
with an expired-token error, just run this again.
"""

import base64
import hashlib
import json
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

import requests

REDIRECT_URI = "http://localhost:8765/callback"
TOKEN_FILE = "swiggy_token.json"


def generate_pkce_pair():
    """PKCE proves the app exchanging the code is the same one that started
    the login - protects against a stolen authorization code being reused."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class _CallbackHandler(BaseHTTPRequestHandler):
    """Catches the one redirect Swiggy sends back after login completes."""
    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)
        self.server.auth_code = params.get("code", [None])[0]
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><body><h2>Login complete - close this tab and check your terminal.</h2></body></html>")

    def log_message(self, format, *args):
        pass  # keep the terminal quiet


def wait_for_redirect():
    server = HTTPServer(("localhost", 8765), _CallbackHandler)
    server.auth_code = None
    server.handle_request()  # blocks until the browser redirect hits this
    return server.auth_code


def register_client():
    """Dynamic Client Registration - Swiggy issues a client_id on the spot,
    no manual application needed for this dev/localhost flow."""
    resp = requests.post(
        "https://mcp.swiggy.com/auth/register",
        json={
            "redirect_uris": [REDIRECT_URI],
            "client_name": "Family Grocery Bot",
            "token_endpoint_auth_method": "none",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["client_id"]


def login():
    print("Registering with Swiggy...")
    client_id = register_client()

    verifier, challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(16)

    auth_url = (
        "https://mcp.swiggy.com/auth/authorize"
        f"?response_type=code&client_id={client_id}&redirect_uri={REDIRECT_URI}"
        f"&code_challenge={challenge}&code_challenge_method=S256&state={state}&scope=mcp:tools"
    )

    print("Opening your browser - log in with the account that should receive every order...")
    webbrowser.open(auth_url)

    code = wait_for_redirect()
    if not code:
        print("[error] No authorization code received - try again.")
        return

    print("Exchanging code for an access token...")
    resp = requests.post(
        "https://mcp.swiggy.com/auth/token",
        json={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
        },
        timeout=15,
    )
    resp.raise_for_status()
    token_data = resp.json()
    token_data["expires_at"] = time.time() + token_data["expires_in"]

    with open(TOKEN_FILE, "w") as f:
        json.dump(token_data, f)

    print(f"Logged in - saved to {TOKEN_FILE}, valid for about 5 days.")


if __name__ == "__main__":
    login()
