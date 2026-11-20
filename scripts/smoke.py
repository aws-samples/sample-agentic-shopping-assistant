#!/usr/bin/env python3
"""Operator-only integration smoke. Invokes the model and writes Memory events."""
import argparse
import json
import os
import time
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("API redirects are refused; configure the final HTTPS endpoint")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--invoke", action="store_true", help="Authorize this billed agent invocation and its Memory writes")
    args = parser.parse_args()
    if not args.invoke:
        parser.error("Explicit --invoke is required. This is not a read-only preflight.")
    endpoint = os.environ["ARIA_API_URL"].rstrip("/")
    if not endpoint.startswith("https://"):
        parser.error("An HTTPS API endpoint is required")
    token = os.environ["ARIA_ACCESS_TOKEN"]
    opener = build_opener(NoRedirect())
    def post(path, body):
        req = Request(endpoint + path, data=json.dumps(body).encode(), method="POST", headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        with opener.open(req, timeout=130) as response:
            return json.load(response)
    session = post("/v1/sessions", {})["session_id"]
    start = time.monotonic()
    try:
        result = post("/v1/invocations", {"session_id": session, "message": "남성 겨울 자켓을 찾아주세요.", "stream": False})
    except HTTPError as exc:
        # A failed turn carries its error code; the message is developer detail and isn't printed.
        raise SystemExit(f"The turn failed: HTTP {exc.code} {error_code(exc)}") from None
    if result.get("stopReason") == "guardrail_intervened":
        raise SystemExit("The guardrail refused the smoke prompt")
    if not result.get("data"):
        raise SystemExit("No assistant response")
    print(json.dumps({"sessionId": session, "durationSeconds": round(time.monotonic()-start, 2), "stopReason": result.get("stopReason"),
                      "responseCharacters": len(result["data"]), "success": True}))


def error_code(exc: HTTPError) -> str:
    """The `code` of a coded API error body, or "" if the body has none."""
    try:
        detail = json.load(exc).get("detail")
    except (ValueError, AttributeError):
        return ""
    return detail.get("code", "") if isinstance(detail, dict) else ""


if __name__ == "__main__":
    main()
