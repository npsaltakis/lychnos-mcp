import { t } from "./i18n";

/** Project virtual-environment Python, as typed from the backend folder on this OS. */
export const VENV_PY = /Windows/i.test(navigator.userAgent) ? "..\\.venv\\Scripts\\python.exe" : "../.venv/bin/python";

/** Name of the OS secret store that holds the eWeLink keys. */
export function secretStoreName(): string {
  if (/Windows/i.test(navigator.userAgent)) return "Windows Credential Manager";
  if (/Mac/i.test(navigator.userAgent)) return "macOS Keychain";
  return t("set.keyringLinux");
}
