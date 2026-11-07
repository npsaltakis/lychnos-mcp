/** Project virtual-environment Python, as typed from the backend folder on this OS. */
export const VENV_PY = /Windows/i.test(navigator.userAgent) ? "..\\.venv\\Scripts\\python.exe" : "../.venv/bin/python";

/** Name of the OS secret store that holds the eWeLink keys. */
export const SECRET_STORE = /Windows/i.test(navigator.userAgent)
  ? "Windows Credential Manager"
  : /Mac/i.test(navigator.userAgent)
    ? "macOS Keychain"
    : "keyring του συστήματος (GNOME Keyring / KWallet)";
