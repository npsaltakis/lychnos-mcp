import { api } from "../api/client";

export function webPushSupported(): boolean {
  return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

/** Web Push needs a secure context: HTTPS, or localhost / 127.0.0.1 on this PC. */
export function webPushContextOk(): boolean {
  return window.isSecureContext;
}

function urlBase64ToBytes(base64: string): Uint8Array<ArrayBuffer> {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const raw = atob((base64 + padding).replace(/-/g, "+").replace(/_/g, "/"));
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

export async function currentSubscription(): Promise<PushSubscription | null> {
  if (!webPushSupported()) return null;
  const reg = await navigator.serviceWorker.getRegistration("/");
  return reg ? reg.pushManager.getSubscription() : null;
}

/** Ask for permission, subscribe this browser and register it with the backend. */
export async function enableWebPush(): Promise<void> {
  if (!webPushSupported()) throw new Error("Ο browser δεν υποστηρίζει ειδοποιήσεις push");
  if (!webPushContextOk()) throw new Error("Χρειάζεται HTTPS (ή άνοιγμα από αυτό το PC στο 127.0.0.1)");
  const permission = await Notification.requestPermission();
  if (permission !== "granted") throw new Error("Δεν δόθηκε άδεια για ειδοποιήσεις στον browser");
  const reg = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
  await navigator.serviceWorker.ready;
  const { public_key } = await api.webpushKey();
  const existing = await reg.pushManager.getSubscription();
  const sub = existing ?? (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToBytes(public_key) }));
  await api.webpushSubscribe(sub.toJSON());
}

export async function disableWebPush(): Promise<void> {
  const sub = await currentSubscription();
  if (!sub) return;
  await api.webpushUnsubscribe(sub.endpoint);
  await sub.unsubscribe();
}
