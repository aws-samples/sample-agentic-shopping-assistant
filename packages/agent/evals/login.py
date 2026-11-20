"""Get an eval user's access token through Cognito managed login (authorization code + PKCE).

    uv run python -m evals.login \\
        --domain https://<prefix>.auth.<region>.amazoncognito.com \\
        --client-id <AuthStack UserPoolClientId> --out ../../artifacts/eval-token

Opens the managed login page in a browser and listens on the web client's
registered localhost callback (default http://localhost:4000/auth/callback),
so the local web dev server must not be running. The access token is written
to --out with owner-only permissions and is never printed. Tokens last one hour.
"""

import argparse
import base64
import hashlib
import json
import os
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

DEFAULT_REDIRECT = "http://localhost:4000/auth/callback"
DEFAULT_SCOPES = "openid aria/chat"


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def authorize_url(domain: str, client_id: str, redirect_uri: str, scopes: str, state: str, challenge: str) -> str:
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scopes,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{domain.rstrip('/')}/oauth2/authorize?{query}"


def wait_for_code(redirect_uri: str, state: str, timeout: float) -> str:
    parsed = urlparse(redirect_uri)
    if parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1"):
        raise ValueError("The callback must be a localhost URL")
    result: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlparse(self.path)
            params = {k: v[0] for k, v in parse_qs(url.query).items()}
            ok = url.path == parsed.path and params.get("state") == state and "code" in params
            if ok:
                result["code"] = params["code"]
            elif url.path == parsed.path:
                result["error"] = params.get("error", "state mismatch or missing code")
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Signed in. You can close this tab." if ok else b"Sign-in failed. Check the terminal.")
            if result:
                threading.Thread(target=self.server.shutdown, daemon=True).start()

        def log_message(self, *args):
            pass

    # Bind loopback only: the callback is never reachable from the network.
    server = HTTPServer(("127.0.0.1", parsed.port or 80), Handler)
    timer = threading.Timer(timeout, server.shutdown)
    timer.start()
    try:
        server.serve_forever()
    finally:
        timer.cancel()
        server.server_close()
    if "code" not in result:
        raise SystemExit(f"Sign-in did not complete: {result.get('error', 'timed out')}")
    return result["code"]


def exchange(domain: str, client_id: str, redirect_uri: str, code: str, verifier: str) -> dict:
    body = urlencode(
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        }
    ).encode()
    token_url = f"{domain.rstrip('/')}/oauth2/token"
    if not token_url.startswith("https://"):
        raise ValueError("The token endpoint must be HTTPS")
    request = Request(token_url, data=body, method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"})
    # The scheme is checked above: only https:// URLs reach urlopen.
    with urlopen(request, timeout=30) as response:  # nosec B310  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected, python.lang.security.audit.dynamic-urllib-use-detected # https:// only, checked above
        return json.load(response)


def write_secret(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(value)
    os.chmod(path, 0o600)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.login", description=__doc__.split("\n")[0])
    parser.add_argument("--domain", required=True, help="Cognito domain, e.g. https://<prefix>.auth.<region>.amazoncognito.com")
    parser.add_argument("--client-id", required=True, help="AuthStack output UserPoolClientId")
    parser.add_argument("--out", type=Path, required=True, help="File to write the access token to (mode 0600)")
    parser.add_argument("--redirect-uri", default=DEFAULT_REDIRECT)
    parser.add_argument("--scopes", default=DEFAULT_SCOPES)
    parser.add_argument("--timeout", type=float, default=300, help="Seconds to wait for sign-in (default 300)")
    parser.add_argument("--no-browser", action="store_true", help="Print the URL instead of opening a browser")
    args = parser.parse_args(argv)
    if not args.domain.startswith("https://"):
        parser.error("The Cognito domain must be HTTPS")

    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    url = authorize_url(args.domain, args.client_id, args.redirect_uri, args.scopes, state, challenge)
    print(f"Sign in as the eval user:\n{url}", flush=True)
    if not args.no_browser:
        webbrowser.open(url)

    code = wait_for_code(args.redirect_uri, state, args.timeout)
    tokens = exchange(args.domain, args.client_id, args.redirect_uri, code, verifier)
    write_secret(args.out, tokens["access_token"])
    print(f"Access token written to {args.out} (expires in {tokens.get('expires_in', '?')} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
