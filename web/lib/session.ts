const KEY = "floodline.citizen";

/** One Citizen node per browser. The id is sent with the route and with reports. */
export function citizenId(): string {
  if (typeof window === "undefined") return "";
  const existing = window.localStorage.getItem(KEY) || "";
  if (/^[A-Za-z0-9_-]{8,80}$/.test(existing)) return existing;
  const next = crypto.randomUUID().replace(/-/g, "");
  window.localStorage.setItem(KEY, next);
  return next;
}
