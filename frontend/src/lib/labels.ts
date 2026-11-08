import { t } from "./i18n";

/** Human label for the "who changed it" field of the change log. */
export function sourceLabel(source: string): string {
  if (source === "dashboard") return t("src.dashboard");
  if (source === "claude") return t("src.claude");
  if (source === "api") return t("src.api");
  if (source === "external") return t("src.external");
  if (source === "system") return t("src.system");
  if (source === "vacation") return t("src.vacation");
  if (source === "telegram") return t("src.telegram");
  if (source === "presence") return t("src.presence");
  const scene = source.match(/^(.*) · scene (.*)$/);
  if (scene) return t("src.scene", { name: scene[2], from: sourceLabel(scene[1]) });
  if (source.startsWith("automation · ")) return t("src.automation", { name: source.slice("automation · ".length) });
  return source;
}

/** Translate the backend's change description ("ON", "brightness 40%", "color #ff0000", "white 2700K"). */
export function changeLabel(command: string): string {
  if (command === "ON") return t("log.cmd.on");
  if (command === "OFF") return t("log.cmd.off");
  if (command === "offline") return t("log.offline");
  if (command === "online") return t("log.online");
  if (command === "effect stop") return t("fx.stopped");
  if (command.startsWith("segments ")) return t("grad.logged");
  if (command.startsWith("music ")) return t("music.logged");
  if (command.startsWith("effect ")) {
    const name = command.slice(7);
    if (name === "rainbow" || name === "breathe" || name === "candle") return t("fx.started", { name: t(`fx.${name}`) });
  }
  const m = command.match(/^(brightness|color|white) (.+)$/);
  if (m) {
    const key = m[1] === "brightness" ? "log.cmd.brightness" : m[1] === "color" ? "log.cmd.color" : "log.cmd.white";
    return t(key, { value: m[2] });
  }
  return command;
}
