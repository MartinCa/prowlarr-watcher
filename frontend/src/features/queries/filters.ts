/** Tri-state media filter: off → include → exclude → off. */
export type MediaFilter = "off" | "include" | "exclude";

export interface QueryFilterState {
  newOnly: boolean;
  audiobook: MediaFilter;
  ebook: MediaFilter;
}

export const NO_FILTERS: QueryFilterState = { newOnly: false, audiobook: "off", ebook: "off" };

export function nextMediaFilter(current: MediaFilter): MediaFilter {
  if (current === "off") return "include";
  if (current === "include") return "exclude";
  return "off";
}

export function matchesMediaFilter(filter: MediaFilter, hasMedia: boolean): boolean {
  if (filter === "include") return hasMedia;
  if (filter === "exclude") return !hasMedia;
  return true;
}

export function hasActiveFilter(filters: QueryFilterState): boolean {
  return filters.newOnly || filters.audiobook !== "off" || filters.ebook !== "off";
}
