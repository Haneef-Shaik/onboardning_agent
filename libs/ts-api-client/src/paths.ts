/** Where the API is mounted on the web origin (the proxy forwards `/api/*` to the API). */
export const API_PREFIX = "/api";

export function apiPath(path: string): string {
  return `${API_PREFIX}/${path.replace(/^\/+/, "")}`;
}
