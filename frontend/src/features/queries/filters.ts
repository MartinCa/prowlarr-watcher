export interface QueryFilterState {
  newOnly: boolean;
  audiobook: boolean;
  ebook: boolean;
}

export const NO_FILTERS: QueryFilterState = { newOnly: false, audiobook: false, ebook: false };

export function hasActiveFilter(filters: QueryFilterState): boolean {
  return filters.newOnly || filters.audiobook || filters.ebook;
}
