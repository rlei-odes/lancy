/** Mirrors MIN_SHARE in backend/src/lancy/language.py: a detected language below it does not count. */
export const CORPUS_LANGUAGE_MIN_SHARE = 0.1;

/**
 * The KB's corpus languages, main one first, with their share of the detected
 * documents. An admin-set list replaces the detected one and carries no shares.
 */
export function corpusLanguageShares(
    counts: Record<string, number> | undefined,
    override: string[] | undefined,
): { code: string; share: number | null }[] {
    if (override && override.length > 0) return override.map((code) => ({ code, share: null }));
    const entries = Object.entries(counts ?? {});
    const total = entries.reduce((sum, [, n]) => sum + n, 0);
    if (total === 0) return [];
    return entries
        .map(([code, n]) => ({ code, share: n / total }))
        .filter((l) => l.share >= CORPUS_LANGUAGE_MIN_SHARE)
        .sort((a, b) => b.share - a.share);
}
