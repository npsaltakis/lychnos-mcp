import { Cpu, House, LogOut, Settings, Sparkles } from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../store/auth";
import { useDevices, type Connection } from "../store/devices";
import { Toasts } from "./Toasts";
import { cx } from "./ui";

const NAV = [
  { to: "/", label: "Αρχική", icon: House },
  { to: "/scenes", label: "Σκηνές", icon: Sparkles },
  { to: "/devices", label: "Συσκευές", icon: Cpu },
  { to: "/settings", label: "Ρυθμίσεις", icon: Settings },
];

const CONNECTION: Record<Connection, { text: string; dot: string }> = {
  online: { text: "Συνδεδεμένο", dot: "bg-emerald-400" },
  connecting: { text: "Σύνδεση…", dot: "bg-amber-300 animate-pulse" },
  offline: { text: "Χωρίς σύνδεση", dot: "bg-red-400" },
};

function ConnectionPill() {
  const connection = useDevices((s) => s.connection);
  const c = CONNECTION[connection];
  return (
    <span className="inline-flex items-center gap-2 rounded-full border border-line px-3 py-1 text-xs text-muted">
      <span className={cx("size-2 rounded-full", c.dot)} aria-hidden />
      {c.text}
    </span>
  );
}

function LogoutButton({ compact }: { compact?: boolean }) {
  const logout = async () => {
    try {
      await api.logout();
    } finally {
      useAuth.getState().set("signed_out");
    }
  };
  return (
    <button
      onClick={logout}
      aria-label="Αποσύνδεση"
      title="Αποσύνδεση"
      className={cx(
        "inline-flex min-h-10 items-center gap-2 rounded-xl px-3 text-sm text-muted transition-colors hover:bg-panel-2 hover:text-white",
        compact && "px-2",
      )}
    >
      <LogOut className="size-4" aria-hidden />
      {!compact && "Αποσύνδεση"}
    </button>
  );
}

export function Layout() {
  return (
    <div className="min-h-screen md:flex">
      {/* Sidebar (tablet/desktop) */}
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r border-line bg-panel/60 p-4 md:flex">
        <div className="mb-8 flex items-center gap-2 px-2">
          <img src="/favicon.svg" alt="" className="size-8" />
          <span className="font-semibold">Lychnos</span>
        </div>
        <nav className="flex flex-col gap-1">
          {NAV.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              className={({ isActive }) =>
                cx(
                  "flex min-h-10 items-center gap-3 rounded-xl px-3 text-sm transition-colors",
                  isActive ? "bg-panel-2 text-white" : "text-muted hover:bg-panel-2 hover:text-white",
                )
              }
            >
              <Icon className="size-4" aria-hidden />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto flex flex-col items-start gap-3">
          <LogoutButton />
          <div className="px-2">
            <ConnectionPill />
          </div>
        </div>
      </aside>

      <div className="flex-1 pb-24 md:pb-10">
        <header className="flex items-center justify-between border-b border-line px-4 py-3 md:hidden">
          <div className="flex items-center gap-2">
            <img src="/favicon.svg" alt="" className="size-7" />
            <span className="font-semibold">Lychnos</span>
          </div>
          <div className="flex items-center gap-1">
            <ConnectionPill />
            <LogoutButton compact />
          </div>
        </header>
        <main className="mx-auto max-w-6xl px-4 py-6 sm:px-6 md:py-10">
          <Outlet />
        </main>
      </div>

      {/* Bottom navigation (mobile) */}
      <nav className="fixed inset-x-0 bottom-0 z-40 grid grid-cols-4 border-t border-line bg-panel/95 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden">
        {NAV.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            className={({ isActive }) =>
              cx("flex min-h-14 flex-col items-center justify-center gap-1 text-[11px]", isActive ? "text-accent" : "text-muted")
            }
          >
            <Icon className="size-5" aria-hidden />
            {label}
          </NavLink>
        ))}
      </nav>
      <Toasts />
    </div>
  );
}
