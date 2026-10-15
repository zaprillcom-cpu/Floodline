export type SavedPlace = { id: string; name: string; lat: number; lon: number };
export type FamilyMember = { id: string; name: string; status: "safe" | "help"; at: string };

export type MeState = { places: SavedPlace[]; family: FamilyMember[] };

const KEY = "floodline.me.v1";

const EMPTY: MeState = { places: [], family: [] };

export function loadMe(): MeState {
  if (typeof window === "undefined") return EMPTY;
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return EMPTY;
    const parsed = JSON.parse(raw) as MeState;
    return {
      places: Array.isArray(parsed.places) ? parsed.places : [],
      family: Array.isArray(parsed.family) ? parsed.family : [],
    };
  } catch {
    return EMPTY;
  }
}

export function saveMe(state: MeState): void {
  window.localStorage.setItem(KEY, JSON.stringify(state));
}
