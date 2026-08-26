// PLM 결함 데이터 캐시 — 한 결함의 상세·코멘트·첨부 목록을 결함당 한 번만 받는다.
//
// Re-entering the PLM view restores the selection, which re-runs the selection
// handler. Without a cache every rerender() — a theme toggle, a file-picker
// change — would refire three calls at the corporate PLM API.
//
// This module holds no DOM and no module-level state: the caller supplies both
// the api object and the store to keep entries in (the view passes
// `ctx.plmState.cache`, so entries outlive the view). That is what makes it
// testable without a browser — see tests/js/plm_data.test.mjs.

/** The one place the cache key format is decided; app.js invalidates by it too. */
export function defectCacheKey(division, defectCode) {
  return `${division}:${defectCode}`;
}

/**
 * @param api    object exposing plmDefectDetails / plmHumanComments / plmFiles
 * @param store  plain object the entries live in, keyed by defectCacheKey()
 */
export function createDefectCache(api, store) {
  return {
    /**
     * Detail and developer comments for one defect.
     *
     * A failed call is deliberately left uncached so the next visit retries
     * rather than freezing an empty panel. Both calls have to land for the
     * entry to be written — caching half of it would serve a detail pane with
     * permanently missing comments.
     */
    async detail(division, defect) {
      const key = defectCacheKey(division, defect.defectCode);
      if (store[key]?.detail) return store[key].detail;

      const [details, comments] = await Promise.all([
        api.plmDefectDetails(division, [defect.defectCode]).catch(() => null),
        api.plmHumanComments(division, defect.defectCode).catch(() => null),
      ]);
      const entry = {
        details: details || { defects: [] },
        comments: comments || { comments: [] },
      };
      if (details && comments) store[key] = { ...store[key], detail: entry };
      return entry;
    },

    /** Attachment listing for one defect. Failures are left uncached. */
    async attachments(division, defect) {
      const key = defectCacheKey(division, defect.defectCode);
      if (store[key]?.files) return store[key].files;

      const listing = await api.plmFiles(division, defect.defectCode).catch(() => null);
      if (listing) store[key] = { ...store[key], files: listing };
      return listing || { files: [] };
    },

    /** Drop one defect — filing a comment changes the list being cached. */
    invalidate(division, defectCode) {
      delete store[defectCacheKey(division, defectCode)];
    },
  };
}
