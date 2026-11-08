"""Store the Govee API key (used only for music mode) in the OS keyring.

    python -m app.integrations.govee_key           # asks for the key (hidden), checks it, stores it
    python -m app.integrations.govee_key --remove  # deletes it

Get a key in the Govee Home app: Profile → Settings → Apply for API Key (sent by email).
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from app.core.secrets import KeyringSecretStore
from app.providers.govee_lan.cloud import SECRET_NAME, GoveeCloud


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--remove", action="store_true", help="delete the stored key")
    args = parser.parse_args()
    store = KeyringSecretStore()
    if args.remove:
        store.delete(SECRET_NAME)
        print("Govee API key removed.")
        return

    key = getpass.getpass("Govee API key (input hidden): ").strip()
    if not key:
        sys.exit("No key entered.")

    async def check() -> list:
        cloud = GoveeCloud(store)
        try:
            return await cloud.devices(key, fresh=True)
        finally:
            await cloud.close()

    try:
        devices = asyncio.run(check())
    except Exception as exc:  # noqa: BLE001 - shown to the user
        sys.exit(f"The key did not work: {exc}")
    store.set(SECRET_NAME, key)
    print("Key stored in the OS keyring. Lights in the account:")
    for dev in devices:
        styles = ", ".join(GoveeCloud.music_options(dev)) or "no music mode"
        print(f"  {dev.get('deviceName')} ({dev.get('sku')}): {styles}")


if __name__ == "__main__":
    main()
