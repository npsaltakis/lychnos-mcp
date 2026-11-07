import { CalendarClock, Cpu, House, LogOut, ScrollText, Settings, Sparkles } from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";
import { api } from "../api/client";
import { useT, type MessageKey } from "../lib/i18n";
import { useAuth } from "../store/auth";
import { useDevices, type Connection } from "../store/devices";
import { Toasts } from "./Toasts";
import { LanguageSwitch, cx } from "./ui";

const NAV: { to: string; label: MessageKey; short?: MessageKey; icon: typeof House }[] = [
  { to: "/", label: "nav.home", icon: House },
  { to: "/scenes", label: "nav.scenes", icon: Sparkles },
  { to: "/automations", label: "nav.automations", short: "nav.automationsShort", icon: CalendarClock },
  { to: "/devices", label: "nav.devices", icon: Cpu },
  { to: "/log", label: "nav.log", icon: ScrollText },
  { to: "/settings", label: "nav.settings", icon: Settings },
];

const CONNECTION: Record<Connection, { key: MessageKey; dot: string }> = {
  online: { key: "conn.online", dot: "bg-emerald-400" },
  connecting: { key: "conn.connecting", dot: "bg-amber-300 animate-pulse" },
  offline: { key: "conn.offline", dot: "bg-red-400" },
};

function ConnectionPill() {
  const t = useT();
  const connection = useDevices((s) => s.connection);
  const c = CONNECTION[connection];
  return (
    <span className="inline-flex items-center gap-2 rounded-full border border-line px-3 py-1 text-xs text-muted">
      <span className={cx("size-2 rounded-full", c.dot)} aria-hidden />
      {t(c.key)}
    </span>
  );
}

function LogoutButton({ compact }: { compact?: boolean }) {
  const t = useT();
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
      aria-label={t("nav.logout")}
      title={t("nav.logout")}
      className={cx(
        "inline-flex min-h-10 items-center gap-2 rounded-xl px-3 text-sm text-muted transition-colors hover:bg-panel-2 hover:text-white",
        compact && "px-2",
      )}
    >
      <LogOut className="size-4" aria-hidden />
      {!compact && t("nav.logout")}
    </button>
  );
}

export function Layout() {
  const t = useT();
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
              {t(label)}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto flex flex-col items-start gap-3">
          <LanguageSwitch className="ml-2" />
          <LogoutButton />
          <div className="px-2">
            <ConnectionPill />
          </div>
        </div>
      </aside>

      <div className="min-w-0 flex-1 pb-24 md:pb-10">
        <header className="flex items-center justify-between gap-2 border-b border-line px-4 py-3 md:hidden">
          <div className="flex items-center gap-2">
            <img src="/favicon.svg" alt="" className="size-7" />
            <span className="font-semibold">Lychnos</span>
          </div>
          <div className="flex items-center gap-1">
            <LanguageSwitch />
            <LogoutButton compact />
          </div>
        </header>
        <main className="mx-auto max-w-6xl px-4 py-6 sm:px-6 md:py-10">
          <Outlet />
        </main>
      </div>

      {/* Bottom navigation (mobile) */}
      <nav className="fixed inset-x-0 bottom-0 z-40 grid grid-cols-6 border-t border-line bg-panel/95 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden">
        {NAV.map(({ to, label, short, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            className={({ isActive }) =>
              cx(
                "flex min-h-14 flex-col items-center justify-center gap-1 px-0.5 text-[10px] leading-tight",
                isActive ? "text-accent" : "text-muted",
              )
            }
          >
            <Icon className="size-5" aria-hidden />
            <span className="max-w-full truncate">{t(short ?? label)}</span>
          </NavLink>
        ))}
      </nav>
      <Toasts />
    </div>
  );
}
