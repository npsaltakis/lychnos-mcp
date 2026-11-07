import { KeyRound, Lock } from "lucide-react";
import { useState } from "react";
import { ApiError, api, checkAuth } from "../api/client";
import { Button, Card, LanguageSwitch } from "../components/ui";
import { useT } from "../lib/i18n";
import { VENV_PY } from "../lib/platform";
import { useAuth } from "../store/auth";

export function Login() {
  const t = useT();
  const status = useAuth((s) => s.status);
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.login(password);
      setPassword("");
      await checkAuth();
    } catch (err) {
      if (!(err instanceof ApiError)) setError(t("login.failed"));
      else if (err.code === "too_many_attempts") setError(t("login.locked", { s: Number(err.details.retry_after_s ?? 30) }));
      else if (err.code === "unauthorized") setError(t("login.wrong"));
      else if (err.code === "network_error") setError(t("common.networkError"));
      else setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 px-4">
      <Card className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center text-center">
          <img src="/favicon.svg" alt="" className="mb-3 size-12" />
          <h1 className="text-xl font-semibold">Lychnos</h1>
          <p className="mt-1 text-sm text-muted">{t("login.subtitle")}</p>
        </div>

        {status === "no_password" ? (
          <div className="space-y-3 text-sm">
            <p className="inline-flex items-center gap-2 font-medium text-amber-300">
              <KeyRound className="size-4" aria-hidden /> {t("login.noPassword")}
            </p>
            <p className="text-muted">{t("login.setPasswordHint")}</p>
            <pre className="overflow-x-auto rounded-xl bg-panel-2 p-3 text-xs">{`${VENV_PY} -m app.auth_cli set-password`}</pre>
            <Button className="w-full" onClick={() => checkAuth()}>
              {t("login.checkAgain")}
            </Button>
          </div>
        ) : (
          <form onSubmit={submit} className="space-y-4">
            <label className="block text-sm">
              <span className="mb-1 block text-muted">{t("login.password")}</span>
              <input
                type="password"
                autoComplete="current-password"
                autoFocus
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="min-h-11 w-full rounded-xl border border-line bg-panel-2 px-3 focus:border-accent focus:outline-none"
              />
            </label>
            {error && (
              <p role="alert" className="text-sm text-red-400">
                {error}
              </p>
            )}
            <Button type="submit" variant="primary" className="w-full" busy={busy} disabled={!password}>
              <Lock className="size-4" aria-hidden /> {t("login.submit")}
            </Button>
          </form>
        )}
      </Card>
      <LanguageSwitch />
    </div>
  );
}
