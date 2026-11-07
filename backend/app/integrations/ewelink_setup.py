"""One-time eWeLink key setup (official eWeLink / CoolKit Open API, OAuth 2.0).

Fetches the per-device ``devicekey`` (needed to encrypt LAN commands) plus UIID/model,
stores the keys in the OS keyring (Windows Credential Manager, macOS Keychain, GNOME Keyring/KWallet
on Linux) and the metadata in the data folder. Keys are never written to .env or the repository.
After this, Sonoff devices are controlled locally; the cloud is not used again.
The OAuth access token is used only during this run and is never stored.

Usage (from the backend folder):
    ..\\.venv\\Scripts\\python.exe -m app.integrations.ewelink_setup

Prerequisite: an app at https://dev.ewelink.cc with redirect URL
    http://127.0.0.1:8765/ewelink/callback   (or pass --redirect-url)
Docs: https://coolkit-technologies.github.io/eWeLink-API/#/en/OAuth2.0
"""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import hmac
import json
import queue
import secrets
import string
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import keyring

from app.core.config import get_settings
from app.providers.ewelink_lan.keystore import KEYRING_SERVICE, EwelinkDeviceInfo, KeyStore

AUTH_PAGE = "https://c2ccdn.coolkit.cc/oauth/index.html"
API_HOSTS = {
    "cn": "https://cn-apia.coolkit.cn",
    "as": "https://as-apia.coolkit.cc",
    "us": "https://us-apia.coolkit.cc",
    "eu": "https://eu-apia.coolkit.cc",
}
DEFAULT_REDIRECT = "http://127.0.0.1:8765/ewelink/callback"
APP_ID_ACCOUNT = "ewelink-app:id"
APP_SECRET_ACCOUNT = "ewelink-app:secret"


def sign(secret: str, message: str) -> str:
    digest = hmac.new(secret.encode(), message.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def nonce() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(8))


def authorization_url(app_id: str, app_secret: str, redirect_url: str, state: str) -> str:
    seq = str(int(time.time() * 1000))
    query = {
        "clientId": app_id,
        "seq": seq,
        "authorization": sign(app_secret, f"{app_id}_{seq}"),
        "redirectUrl": redirect_url,
        "grantType": "authorization_code",
        "state": state,
        "nonce": nonce(),
        "showQRCode": "false",
    }
    return f"{AUTH_PAGE}?{urlencode(query)}"


def wait_for_callback(redirect_url: str, state: str, timeout_s: int = 300) -> tuple[str, str]:
    """Serve the redirect URL locally and return (code, region)."""
    parsed = urlparse(redirect_url)
    results: queue.Queue[dict[str, str]] = queue.Queue()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib name
            url = urlparse(self.path)
            if url.path != parsed.path:
                self.send_error(404)
                return
            params = {k: v[0] for k, v in parse_qs(url.query).items()}
            results.put(params)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                "<h2>eWeLink: OK</h2><p>Μπορείς να κλείσεις αυτή τη σελίδα.</p>".encode()
            )

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer((parsed.hostname or "127.0.0.1", parsed.port or 80), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        params = results.get(timeout=timeout_s)
    finally:
        server.shutdown()
    if params.get("state") != state:
        raise SystemExit("State mismatch in OAuth callback; aborting.")
    if "code" not in params or "region" not in params:
        raise SystemExit(f"Unexpected callback parameters: {sorted(params)}")
    return params["code"], params["region"]


def manual_code() -> tuple[str, str]:
    print("\nAfter logging in, copy the FULL address of the page you were sent to and paste it here")
    print("(the code expires 30 seconds after login, be quick):")
    pasted = input("> ").strip()
    params = {k: v[0] for k, v in parse_qs(urlparse(pasted).query).items()}
    if "code" not in params or "region" not in params:
        raise SystemExit("That address does not contain code/region.")
    return params["code"], params["region"]


def exchange_code(app_id: str, app_secret: str, region: str, code: str, redirect_url: str) -> str:
    body = json.dumps(
        {"code": code, "redirectUrl": redirect_url, "grantType": "authorization_code"},
        separators=(",", ":"),
    )
    response = httpx.post(
        f"{API_HOSTS[region]}/v2/user/oauth/token",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-CK-Appid": app_id,
            "X-CK-Nonce": nonce(),
            "Authorization": f"Sign {sign(app_secret, body)}",
        },
        timeout=15,
    )
    payload = response.json()
    if payload.get("error") != 0:
        raise SystemExit(f"Token exchange failed: error {payload.get('error')} {payload.get('msg')}")
    return payload["data"]["accessToken"]


def fetch_things(app_id: str, region: str, access_token: str) -> list[dict[str, Any]]:
    response = httpx.get(
        f"{API_HOSTS[region]}/v2/device/thing",
        params={"num": 0},
        headers={
            "X-CK-Appid": app_id,
            "X-CK-Nonce": nonce(),
            "Authorization": f"Bearer {access_token}",
        },
        timeout=15,
    )
    payload = response.json()
    if payload.get("error") != 0:
        raise SystemExit(f"Device list failed: error {payload.get('error')} {payload.get('msg')}")
    return payload["data"]["thingList"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--redirect-url", default=DEFAULT_REDIRECT)
    parser.add_argument("--manual", action="store_true", help="Paste the redirect URL instead of a local listener")
    args = parser.parse_args()

    app_id = keyring.get_password(KEYRING_SERVICE, APP_ID_ACCOUNT) or input("eWeLink APPID: ").strip()
    app_secret = keyring.get_password(KEYRING_SERVICE, APP_SECRET_ACCOUNT) or getpass.getpass(
        "eWeLink APP SECRET (hidden): "
    ).strip()
    if not app_id or not app_secret:
        raise SystemExit("APPID and APP SECRET are required.")

    state = secrets.token_hex(8)
    url = authorization_url(app_id, app_secret, args.redirect_url, state)
    local = urlparse(args.redirect_url).hostname in ("127.0.0.1", "localhost") and not args.manual
    print("\nOpening the official eWeLink login page in your browser...")
    print(url)
    webbrowser.open(url)

    code, region = wait_for_callback(args.redirect_url, state) if local else manual_code()
    if region not in API_HOSTS:
        raise SystemExit(f"Unknown region {region!r}")

    access_token = exchange_code(app_id, app_secret, region, code, args.redirect_url)
    things = fetch_things(app_id, region, access_token)

    # Credentials worked: remember the app id/secret in the OS keyring for future re-runs.
    keyring.set_password(KEYRING_SERVICE, APP_ID_ACCOUNT, app_id)
    keyring.set_password(KEYRING_SERVICE, APP_SECRET_ACCOUNT, app_secret)

    store = KeyStore(get_settings().data_dir)
    saved: list[EwelinkDeviceInfo] = []
    for thing in things:
        if thing.get("itemType") not in (1, 2):
            continue  # groups
        item = thing["itemData"]
        deviceid, devicekey = item.get("deviceid"), item.get("devicekey")
        if not deviceid or not devicekey:
            continue
        store.set_devicekey(deviceid, devicekey)
        uiid = (item.get("extra") or {}).get("uiid")
        info = EwelinkDeviceInfo(
            deviceid=deviceid,
            name=item.get("name") or deviceid,
            uiid=int(uiid) if uiid is not None else None,
            model=item.get("productModel"),
            brand=item.get("brandName"),
        )
        saved.append(info)
    store.save(saved)

    print(f"\nSaved {len(saved)} device(s) (keys in the OS keyring):")
    for info in saved:
        print(f"  {info.deviceid}  {info.name!r}  model={info.model}  uiid={info.uiid}")
    print("\nDone. Restart the backend to use the keys.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(1)
