import { Bell, Mail, MonitorSmartphone, Send, Shuffle, Smartphone } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { ApiError, api } from "../api/client";
import type { Channel, NotificationSettings, NotificationView } from "../api/types";
import { reportError } from "../lib/commands";
import { currentSubscription, disableWebPush, enableWebPush, webPushContextOk, webPushSupported } from "../lib/webpush";
import { t, useT, type MessageKey } from "../lib/i18n";
import { notify } from "../store/toasts";
import { Button, Card, Toggle, cx } from "./ui";

type Secrets = { ntfy_token: string; telegram_token: string; smtp_password: string };
const NO_SECRETS: Secrets = { ntfy_token: "", telegram_token: "", smtp_password: "" };

const EVENTS: { key: keyof NotificationSettings["events"]; hint?: MessageKey }[] = [
  { key: "device_offline", hint: "ntf.ev.device_offlineHint" },
  { key: "device_online" },
  { key: "automation_failed" },
  { key: "automation_ran", hint: "ntf.ev.frequent" },
  { key: "command_failed", hint: "ntf.ev.frequent" },
  { key: "weight", hint: "ntf.ev.weightHint" },
];

const input = "min-h-10 w-full rounded-xl border border-line bg-panel-2 px-3 text-sm focus:border-accent focus:outline-none";

function Field({ label, children, wide }: { label: string; children: ReactNode; wide?: boolean }) {
  return (
    <label className={cx("block text-sm", wide && "sm:col-span-2")}>
      <span className="mb-1 block text-muted">{label}</span>
      {children}
    </label>
  );
}

function SecretInput({
  id,
  saved,
  value,
  onChange,
  onClear,
  placeholder,
}: {
  id: string;
  saved: boolean;
  value: string;
  onChange: (v: string) => void;
  onClear: () => void;
  placeholder: string;
}) {
  return (
    <div className="flex gap-2">
      <input
        id={id}
        type="password"
        autoComplete="off"
        className={input}
        value={value}
        placeholder={saved ? t("ntf.secretSaved") : placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
      {saved && (
        <Button type="button" variant="ghost" className="border border-line" onClick={onClear}>
          {t("ntf.remove")}
        </Button>
      )}
    </div>
  );
}

function Channel({
  icon,
  title,
  description,
  enabled,
  onToggle,
  onTest,
  testing,
  children,
}: {
  icon: ReactNode;
  title: string;
  description: string;
  enabled: boolean;
  onToggle: (on: boolean) => void;
  onTest: () => void;
  testing: boolean;
  children: ReactNode;
}) {
  return (
    <div className="rounded-xl border border-line p-4">
      <div className="flex items-start gap-3">
        <div className="mt-0.5 text-accent">{icon}</div>
        <div className="min-w-0 flex-1">
          <h3 className="font-medium">{title}</h3>
          <p className="text-sm text-muted">{description}</p>
        </div>
        <Toggle label={t("ntf.enabledAria", { name: title })} checked={enabled} onChange={onToggle} />
      </div>
      {enabled && (
        <div className="mt-4 space-y-3">
          {children}
          <Button type="button" variant="ghost" className="border border-line" busy={testing} onClick={onTest}>
            <Send className="size-4" aria-hidden /> {t("ntf.test")}
          </Button>
        </div>
      )}
    </div>
  );
}

function randomTopic(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(9));
  return "lychnos-" + Array.from(bytes, (b) => b.toString(36).padStart(2, "0")).join("").slice(0, 14);
}

export function NotificationsCard() {
  const tr = useT();
  const [saved, setSaved] = useState<NotificationView | null>(null);
  const [form, setForm] = useState<NotificationSettings | null>(null);
  const [secrets, setSecrets] = useState<Secrets>(NO_SECRETS);
  const [cleared, setCleared] = useState<Set<keyof Secrets>>(new Set());
  const [busy, setBusy] = useState<string | null>(null);
  const [chats, setChats] = useState<{ id: string; name: string }[]>([]);
  const [thisBrowser, setThisBrowser] = useState(false);

  const load = (view: NotificationView) => {
    setSaved(view);
    const { secrets_set: _s, webpush_devices: _w, ...settings } = view;
    setForm(settings);
    setSecrets(NO_SECRETS);
    setCleared(new Set());
  };

  useEffect(() => {
    api.notifications().then(load).catch((err) => reportError(null, err));
    currentSubscription().then((s) => setThisBrowser(!!s)).catch(() => undefined);
  }, []);

  if (!form || !saved) return null;

  const set = <K extends keyof NotificationSettings>(key: K, patch: Partial<NotificationSettings[K]>) =>
    setForm((f) => (f ? { ...f, [key]: { ...f[key], ...patch } } : f));

  const secretPayload = () =>
    Object.fromEntries(
      (Object.keys(NO_SECRETS) as (keyof Secrets)[]).map((k) => [k, cleared.has(k) ? "" : secrets[k] ? secrets[k] : null]),
    );

  const save = async (quiet = false): Promise<boolean> => {
    setBusy("save");
    try {
      load(await api.saveNotifications({ ...form, secrets: secretPayload() }));
      if (!quiet) notify("success", t("ntf.saved"));
      return true;
    } catch (err) {
      reportError(null, err);
      return false;
    } finally {
      setBusy(null);
    }
  };

  const test = async (channel: Channel) => {
    if (!(await save(true))) return;
    setBusy(`test-${channel}`);
    try {
      const result = await api.testNotification(channel);
      const message = result[channel];
      if (message === "ok") notify("success", t("ntf.testSent"), t("ntf.testSentText"));
      else notify("error", t("ntf.testFailed"), message);
    } catch (err) {
      reportError(null, err);
    } finally {
      setBusy(null);
    }
  };

  const findChats = async () => {
    setBusy("chats");
    try {
      const found = await api.telegramChats(secrets.telegram_token || undefined);
      setChats(found);
      if (found.length === 1) set("telegram", { chat_id: found[0].id });
      if (!found.length) notify("warning", t("ntf.tgNone"), t("ntf.tgNoneText"));
    } catch (err) {
      reportError(null, err);
    } finally {
      setBusy(null);
    }
  };

  const toggleBrowser = async () => {
    setBusy("browser");
    try {
      if (thisBrowser) {
        await disableWebPush();
        setThisBrowser(false);
      } else {
        await enableWebPush();
        setThisBrowser(true);
        notify("success", t("ntf.browserOk"));
      }
      load(await api.notifications());
    } catch (err) {
      if (err instanceof ApiError) reportError(null, err);
      else notify("error", t("ntf.browser"), err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  const secretProps = (key: keyof Secrets, placeholder: string) => ({
    id: `secret-${key}`,
    saved: saved.secrets_set[key] && !cleared.has(key),
    value: secrets[key],
    placeholder,
    onChange: (v: string) => {
      setSecrets((s) => ({ ...s, [key]: v }));
      setCleared((c) => {
        const n = new Set(c);
        n.delete(key);
        return n;
      });
    },
    onClear: () => setCleared((c) => new Set(c).add(key)),
  });

  return (
    <Card className="space-y-5 lg:col-span-2">
      <div className="flex items-start gap-3">
        <Bell className="mt-0.5 size-5 text-accent" aria-hidden />
        <div>
          <h2 className="font-semibold">{tr("ntf.title")}</h2>
          <p className="text-sm text-muted">{tr("ntf.intro")}</p>
        </div>
      </div>

      <fieldset>
        <legend className="mb-2 text-sm text-muted">{tr("ntf.events")}</legend>
        <div className="grid gap-2 sm:grid-cols-2">
          {EVENTS.map((e) => (
            <label key={e.key} className="inline-flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                className="size-4 accent-amber-400"
                checked={form.events[e.key]}
                onChange={(ev) => set("events", { [e.key]: ev.target.checked })}
              />
              {tr(`ntf.ev.${e.key}` as MessageKey)}
              {e.hint && <span className="text-xs text-muted">({tr(e.hint)})</span>}
            </label>
          ))}
        </div>
      </fieldset>

      <div className="space-y-3">
        <Channel
          icon={<Smartphone className="size-5" />}
          title="ntfy"
          description={tr("ntf.ntfyDesc")}
          enabled={form.ntfy.enabled}
          onToggle={(on) => set("ntfy", { enabled: on, topic: form.ntfy.topic || randomTopic() })}
          onTest={() => test("ntfy")}
          testing={busy === "test-ntfy"}
        >
          <p className="text-sm text-muted">{tr("ntf.ntfyHelp")}</p>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label={tr("ntf.topic")}>
              <div className="flex gap-2">
                <input id="ntfy-topic" className={input} value={form.ntfy.topic} maxLength={64} onChange={(e) => set("ntfy", { topic: e.target.value.replace(/[^A-Za-z0-9_-]/g, "") })} />
                <Button type="button" variant="ghost" className="border border-line" aria-label={tr("ntf.random")} onClick={() => set("ntfy", { topic: randomTopic() })}>
                  <Shuffle className="size-4" />
                </Button>
              </div>
            </Field>
            <Field label="Server">
              <input id="ntfy-server" className={input} value={form.ntfy.server} onChange={(e) => set("ntfy", { server: e.target.value })} />
            </Field>
            <Field label={tr("ntf.token")} wide>
              <SecretInput {...secretProps("ntfy_token", tr("ntf.optional"))} />
            </Field>
          </div>
        </Channel>

        <Channel
          icon={<Send className="size-5" />}
          title="Telegram"
          description={tr("ntf.tgDesc")}
          enabled={form.telegram.enabled}
          onToggle={(on) => set("telegram", { enabled: on })}
          onTest={() => test("telegram")}
          testing={busy === "test-telegram"}
        >
          <ol className="list-decimal space-y-1 pl-5 text-sm text-muted">
            <li>{tr("ntf.tgStep1")}</li>
            <li>{tr("ntf.tgStep2")}</li>
            <li>{tr("ntf.tgStep3")}</li>
          </ol>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label={tr("ntf.tgToken")} wide>
              <SecretInput {...secretProps("telegram_token", "123456:ABC…")} />
            </Field>
            <Field label="Chat ID">
              <div className="flex gap-2">
                <input id="tg-chat" className={input} value={form.telegram.chat_id} onChange={(e) => set("telegram", { chat_id: e.target.value.replace(/[^\d-]/g, "") })} />
                <Button type="button" variant="ghost" className="border border-line whitespace-nowrap" busy={busy === "chats"} onClick={findChats}>
                  {tr("ntf.tgFind")}
                </Button>
              </div>
            </Field>
            {chats.length > 1 && (
              <Field label={tr("ntf.tgFound")}>
                <select className={input} value={form.telegram.chat_id} onChange={(e) => set("telegram", { chat_id: e.target.value })}>
                  {chats.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name || c.id}
                    </option>
                  ))}
                </select>
              </Field>
            )}
          </div>
          <label className="mt-3 flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              className="mt-0.5 size-4 accent-amber-400"
              checked={form.telegram.commands}
              onChange={(e) => set("telegram", { commands: e.target.checked })}
            />
            <span>
              {tr("ntf.tgCommands")}
              <span className="block text-muted">{tr("ntf.tgCommandsHint")}</span>
            </span>
          </label>
        </Channel>

        <Channel
          icon={<Mail className="size-5" />}
          title="Email"
          description={tr("ntf.mailDesc")}
          enabled={form.email.enabled}
          onToggle={(on) => set("email", { enabled: on })}
          onTest={() => test("email")}
          testing={busy === "test-email"}
        >
          <p className="text-sm text-muted">{tr("ntf.mailHelp")}</p>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label={tr("ntf.smtpServer")}>
              <input id="smtp-host" className={input} value={form.email.smtp_host} placeholder="smtp.gmail.com" onChange={(e) => set("email", { smtp_host: e.target.value.trim() })} />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label={tr("ntf.port")}>
                <input id="smtp-port" type="number" className={input} value={form.email.smtp_port} onChange={(e) => set("email", { smtp_port: Number(e.target.value) })} />
              </Field>
              <Field label={tr("ntf.security")}>
                <select id="smtp-sec" className={input} value={form.email.security} onChange={(e) => set("email", { security: e.target.value as "starttls" | "ssl" | "none" })}>
                  <option value="starttls">STARTTLS</option>
                  <option value="ssl">SSL</option>
                  <option value="none">{tr("ntf.none")}</option>
                </select>
              </Field>
            </div>
            <Field label={tr("ntf.username")}>
              <input id="smtp-user" className={input} autoComplete="off" value={form.email.username} onChange={(e) => set("email", { username: e.target.value.trim() })} />
            </Field>
            <Field label={tr("ntf.password")}>
              <SecretInput {...secretProps("smtp_password", tr("ntf.passwordPlaceholder"))} />
            </Field>
            <Field label={tr("ntf.sender")}>
              <input id="smtp-from" className={input} value={form.email.sender} onChange={(e) => set("email", { sender: e.target.value.trim() })} />
            </Field>
            <Field label={tr("ntf.recipient")}>
              <input id="smtp-to" className={input} value={form.email.recipient} onChange={(e) => set("email", { recipient: e.target.value.trim() })} />
            </Field>
          </div>
        </Channel>

        <Channel
          icon={<MonitorSmartphone className="size-5" />}
          title={tr("ntf.browser")}
          description={tr("ntf.browserDesc")}
          enabled={form.webpush.enabled}
          onToggle={(on) => set("webpush", { enabled: on })}
          onTest={() => test("webpush")}
          testing={busy === "test-webpush"}
        >
          {!webPushSupported() ? (
            <p className="text-sm text-amber-300">{tr("ntf.browserUnsupported")}</p>
          ) : !webPushContextOk() ? (
            <p className="text-sm text-amber-300">{tr("ntf.browserHttps")}</p>
          ) : (
            <div className="flex flex-wrap items-center gap-3">
              <Button type="button" variant={thisBrowser ? "ghost" : "primary"} className={cx(thisBrowser && "border border-line")} busy={busy === "browser"} onClick={toggleBrowser}>
                {thisBrowser ? tr("ntf.browserDisable") : tr("ntf.browserEnable")}
              </Button>
              <span className="text-sm text-muted">{tr("ntf.browserCount", { n: saved.webpush_devices })}</span>
            </div>
          )}
        </Channel>
      </div>

      <div className="flex justify-end">
        <Button variant="primary" busy={busy === "save"} onClick={() => save()}>
          {tr("ntf.saveAll")}
        </Button>
      </div>
    </Card>
  );
}
