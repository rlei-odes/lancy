import { Source } from "@/services/message";

// Resolve the click-target URL for a cited source file.
// If the chunk's metadata carries an `external_url` (DMS deep link or any
// custom URL scheme registered on the user's machine), use it as-is.
// Otherwise fall back to the backend file-serving endpoint.
export function resolveSourceUrl(
    filename: string,
    metadata: Source["metadata"] | undefined | null,
): string {
    const external = metadata?.external_url;
    if (typeof external === "string" && external.length > 0) return external;
    return `/api/v1/files/${encodeURIComponent(filename)}`;
}
