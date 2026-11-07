import { Loader2 } from "lucide-react";
import { useEffect } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { checkAuth } from "./api/client";
import { Layout } from "./components/Layout";
import { useLiveUpdates } from "./hooks/useLiveUpdates";
import { t } from "./lib/i18n";
import { Automations } from "./pages/Automations";
import { Dashboard } from "./pages/Dashboard";
import { Devices } from "./pages/Devices";
import { History } from "./pages/History";
import { Login } from "./pages/Login";
import { Scenes } from "./pages/Scenes";
import { Settings } from "./pages/Settings";
import { useAuth } from "./store/auth";

function SignedInApp() {
  useLiveUpdates();
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="scenes" element={<Scenes />} />
          <Route path="automations" element={<Automations />} />
          <Route path="log" element={<History />} />
          <Route path="history" element={<History />} />
          <Route path="devices" element={<Devices />} />
          <Route path="settings" element={<Settings />} />
          <Route path="*" element={<Dashboard />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

export function App() {
  const status = useAuth((s) => s.status);
  useEffect(() => {
    void checkAuth();
  }, []);

  if (status === "checking") {
    return (
      <div className="flex min-h-screen items-center justify-center text-muted">
        <Loader2 className="size-6 animate-spin" aria-label={t("app.loading")} />
      </div>
    );
  }
  return status === "signed_in" ? <SignedInApp /> : <Login />;
}
