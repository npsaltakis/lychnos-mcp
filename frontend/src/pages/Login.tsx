import { KeyRound, Lock } from "lucide-react";
import { useState } from "react";
import { ApiError, api, checkAuth } from "../api/client";
import { Button, Card } from "../components/ui";
import { VENV_PY } from "../lib/platform";
import { useAuth } from "../store/auth";

export function Login() {
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
      setError(err instanceof ApiError ? err.message : "Σφάλμα σύνδεσης");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <Card className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center text-center">
          <img src="/favicon.svg" alt="" className="mb-3 size-12" />
          <h1 className="text-xl font-semibold">Lychnos</h1>
          <p className="mt-1 text-sm text-muted">Σύνδεση για έλεγχο των φώτων</p>
        </div>

        {status === "no_password" ? (
          <div className="space-y-3 text-sm">
            <p className="inline-flex items-center gap-2 font-medium text-amber-300">
              <KeyRound className="size-4" aria-hidden /> Δεν έχει οριστεί κωδικός
            </p>
            <p className="text-muted">Όρισε κωδικό από τερματικό στον φάκελο backend:</p>
            <pre className="overflow-x-auto rounded-xl bg-panel-2 p-3 text-xs">
              {`${VENV_PY} -m app.auth_cli set-password`}
            </pre>
            <Button className="w-full" onClick={() => checkAuth()}>
              Έλεγχος ξανά
            </Button>
          </div>
        ) : (
          <form onSubmit={submit} className="space-y-4">
            <label className="block text-sm">
              <span className="mb-1 block text-muted">Κωδικός</span>
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
              <Lock className="size-4" aria-hidden /> Σύνδεση
            </Button>
          </form>
        )}
      </Card>
    </div>
  );
}
