/** Human label for the "who changed it" field of the activity log. */
export function sourceLabel(source: string): string {
  if (source === "dashboard") return "Dashboard";
  if (source === "claude") return "Claude";
  if (source === "api") return "API";
  const scene = source.match(/^(.*) · scene (.*)$/);
  if (scene) return `Σκηνή «${scene[2]}» (${sourceLabel(scene[1])})`;
  if (source.startsWith("automation · ")) return `Αυτοματισμός «${source.slice("automation · ".length)}»`;
  return source;
}
