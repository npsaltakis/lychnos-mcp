import { Bell, Mail, MonitorSmartphone, Send, Shuffle, Smartphone } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { ApiError, api } from "../api/client";
import type { Channel, NotificationSettings, NotificationView } from "../api/types";
import { reportError } from "../lib/commands";
import { currentSubscription, disableWebPush, enableWebPush, webPushContextOk, webPushSupported } from "../lib/webpush";
import { notify } from "../store/toasts";
import { Button, Card, Toggle, cx } from "./ui";

type Secrets = { ntfy_token: string; telegram_token: string; smtp_password: string };
const NO_SECRETS: Secrets = { ntfy_token: "", telegram_token: "", smtp_password: "" };

const EVENTS: { key: keyof NotificationSettings["events"]; label: string; hint: string }[] = [
  { key: "device_offline", label: "Συσκευή εκτός δικτύου", hint: "π.χ. κόπηκε το ρεύμα" },
  { key: "device_online", label: "Συσκευή ξανά online", hint: "" },
  { key: "automation_failed", label: "Αποτυχία αυτοματισμού", hint: "" },
  { key: "automation_ran", label: "Κάθε εκτέλεση αυτοματισμού", hint: "μπορεί να είναι συχνό" },
  { key: "command_failed", label: "Αποτυχία εντολής", hint: "μπορεί να είναι συχνό" },
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
        placeholder={saved ? "•••••• αποθηκευμένο (γράψε για αλλαγή)" : placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
      {saved && (
        <Button type="button" variant="ghost" className="border border-line" onClick={onClear}>
          Αφαίρεση
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
        <Toggle label={`${title} ενεργό`} checked={enabled} onChange={onToggle} />
      </div>
      {enabled && (
        <div className="mt-4 space-y-3">
          {children}
          <Button type="button" variant="ghost" className="border border-line" busy={testing} onClick={onTest}>
            <Send className="size-4" aria-hidden /> Δοκιμή
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
      if (!quiet) notify("success", "Οι ειδοποιήσεις αποθηκεύτηκαν");
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
      if (message === "ok") notify("success", "Στάλθηκε δοκιμαστική ειδοποίηση", "Έλεγξε τη συσκευή σου.");
      else notify("error", "Η δοκιμή απέτυχε", message);
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
      if (!found.length) notify("warning", "Δεν βρέθηκε συνομιλία", "Στείλε πρώτα /start στο bot σου από το Telegram και ξαναδοκίμασε.");
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
        notify("success", "Αυτός ο browser θα λαμβάνει ειδοποιήσεις");
      }
      load(await api.notifications());
    } catch (err) {
      if (err instanceof ApiError) reportError(null, err);
      else notify("error", "Ειδοποιήσεις browser", err instanceof Error ? err.message : String(err));
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
          <h2 className="font-semibold">Ειδοποιήσεις</h2>
          <p className="text-sm text-muted">
            Όλα τα κανάλια είναι απενεργοποιημένα μέχρι να τα ανοίξεις. Κωδικοί και tokens αποθηκεύονται στο keyring του
            συστήματος, όχι στη βάση.
          </p>
        </div>
      </div>

      <fieldset>
        <legend className="mb-2 text-sm text-muted">Για ποια γεγονότα</legend>
        <div className="grid gap-2 sm:grid-cols-2">
          {EVENTS.map((e) => (
            <label key={e.key} className="inline-flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                className="size-4 accent-amber-400"
                checked={form.events[e.key]}
                onChange={(ev) => set("events", { [e.key]: ev.target.checked })}
              />
              {e.label}
              {e.hint && <span className="text-xs text-muted">({e.hint})</span>}
            </label>
          ))}
        </div>
      </fieldset>

      <div className="space-y-3">
        <Channel
          icon={<Smartphone className="size-5" />}
          title="ntfy"
          description="Δωρεάν εφαρμογή ειδοποιήσεων για Android/iPhone, χωρίς λογαριασμό."
          enabled={form.ntfy.enabled}
          onToggle={(on) => set("ntfy", { enabled: on, topic: form.ntfy.topic || randomTopic() })}
          onTest={() => test("ntfy")}
          testing={busy === "test-ntfy"}
        >
          <p className="text-sm text-muted">
            Εγκατάστησε την εφαρμογή <b>ntfy</b>, πάτα «+» και κάνε Subscribe στο θέμα παρακάτω. Όποιος ξέρει το όνομα
            του θέματος βλέπει τις ειδοποιήσεις, γι' αυτό κράτα το τυχαίο.
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Θέμα (topic)">
              <div className="flex gap-2">
                <input id="ntfy-topic" className={input} value={form.ntfy.topic} maxLength={64} onChange={(e) => set("ntfy", { topic: e.target.value.replace(/[^A-Za-z0-9_-]/g, "") })} />
                <Button type="button" variant="ghost" className="border border-line" aria-label="Τυχαίο όνομα" onClick={() => set("ntfy", { topic: randomTopic() })}>
                  <Shuffle className="size-4" />
                </Button>
              </div>
            </Field>
            <Field label="Server">
              <input id="ntfy-server" className={input} value={form.ntfy.server} onChange={(e) => set("ntfy", { server: e.target.value })} />
            </Field>
            <Field label="Access token (μόνο για δικό σου/κλειδωμένο server)" wide>
              <SecretInput {...secretProps("ntfy_token", "προαιρετικό")} />
            </Field>
          </div>
        </Channel>

        <Channel
          icon={<Send className="size-5" />}
          title="Telegram"
          description="Μηνύματα από δικό σου bot στο Telegram."
          enabled={form.telegram.enabled}
          onToggle={(on) => set("telegram", { enabled: on })}
          onTest={() => test("telegram")}
          testing={busy === "test-telegram"}
        >
          <ol className="list-decimal space-y-1 pl-5 text-sm text-muted">
            <li>Στο Telegram άνοιξε τον <b>@BotFather</b>, στείλε <code>/newbot</code> και αντέγραψε το token.</li>
            <li>Άνοιξε το νέο bot σου και στείλε του <code>/start</code>.</li>
            <li>Επικόλλησε το token εδώ και πάτα «Εύρεση συνομιλίας».</li>
          </ol>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Bot token" wide>
              <SecretInput {...secretProps("telegram_token", "123456:ABC…")} />
            </Field>
            <Field label="Chat ID">
              <div className="flex gap-2">
                <input id="tg-chat" className={input} value={form.telegram.chat_id} onChange={(e) => set("telegram", { chat_id: e.target.value.replace(/[^\d-]/g, "") })} />
                <Button type="button" variant="ghost" className="border border-line whitespace-nowrap" busy={busy === "chats"} onClick={findChats}>
                  Εύρεση συνομιλίας
                </Button>
              </div>
            </Field>
            {chats.length > 1 && (
              <Field label="Βρέθηκαν">
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
        </Channel>

        <Channel
          icon={<Mail className="size-5" />}
          title="Email"
          description="Μέσω SMTP, π.χ. Gmail με App Password."
          enabled={form.email.enabled}
          onToggle={(on) => set("email", { enabled: on })}
          onTest={() => test("email")}
          testing={busy === "test-email"}
        >
          <p className="text-sm text-muted">
            Για Gmail: server <code>smtp.gmail.com</code>, θύρα 587, STARTTLS, και κωδικός από τα «App passwords» του
            λογαριασμού Google (όχι ο κανονικός σου κωδικός).
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="SMTP server">
              <input id="smtp-host" className={input} value={form.email.smtp_host} placeholder="smtp.gmail.com" onChange={(e) => set("email", { smtp_host: e.target.value.trim() })} />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Θύρα">
                <input id="smtp-port" type="number" className={input} value={form.email.smtp_port} onChange={(e) => set("email", { smtp_port: Number(e.target.value) })} />
              </Field>
              <Field label="Ασφάλεια">
                <select id="smtp-sec" className={input} value={form.email.security} onChange={(e) => set("email", { security: e.target.value as "starttls" | "ssl" | "none" })}>
                  <option value="starttls">STARTTLS</option>
                  <option value="ssl">SSL</option>
                  <option value="none">Καμία</option>
                </select>
              </Field>
            </div>
            <Field label="Όνομα χρήστη">
              <input id="smtp-user" className={input} autoComplete="off" value={form.email.username} onChange={(e) => set("email", { username: e.target.value.trim() })} />
            </Field>
            <Field label="Κωδικός (App Password)">
              <SecretInput {...secretProps("smtp_password", "κωδικός SMTP")} />
            </Field>
            <Field label="Αποστολέας">
              <input id="smtp-from" className={input} value={form.email.sender} onChange={(e) => set("email", { sender: e.target.value.trim() })} />
            </Field>
            <Field label="Παραλήπτης">
              <input id="smtp-to" className={input} value={form.email.recipient} onChange={(e) => set("email", { recipient: e.target.value.trim() })} />
            </Field>
          </div>
        </Channel>

        <Channel
          icon={<MonitorSmartphone className="size-5" />}
          title="Ειδοποιήσεις browser"
          description="Ειδοποιήσεις από το ίδιο το dashboard, χωρίς επιπλέον εφαρμογή."
          enabled={form.webpush.enabled}
          onToggle={(on) => set("webpush", { enabled: on })}
          onTest={() => test("webpush")}
          testing={busy === "test-webpush"}
        >
          {!webPushSupported() ? (
            <p className="text-sm text-amber-300">Αυτός ο browser δεν υποστηρίζει ειδοποιήσεις push.</p>
          ) : !webPushContextOk() ? (
            <p className="text-sm text-amber-300">
              Χρειάζεται HTTPS. Στο PC άνοιξε το dashboard από <code>http://127.0.0.1:3000</code>. Στο κινητό θα
              δουλέψει μέσω Tailscale (HTTPS).
            </p>
          ) : (
            <div className="flex flex-wrap items-center gap-3">
              <Button type="button" variant={thisBrowser ? "ghost" : "primary"} className={cx(thisBrowser && "border border-line")} busy={busy === "browser"} onClick={toggleBrowser}>
                {thisBrowser ? "Απενεργοποίηση σε αυτόν τον browser" : "Ενεργοποίηση σε αυτόν τον browser"}
              </Button>
              <span className="text-sm text-muted">Εγγεγραμμένοι browsers: {saved.webpush_devices}</span>
            </div>
          )}
        </Channel>
      </div>

      <div className="flex justify-end">
        <Button variant="primary" busy={busy === "save"} onClick={() => save()}>
          Αποθήκευση ειδοποιήσεων
        </Button>
      </div>
    </Card>
  );
}
