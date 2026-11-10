/** Placeholder until the client is generated from the OpenAPI contract. */
export const API_PREFIX = "/api";

export function apiPath(path: string): string {
  return `${API_PREFIX}/${path.replace(/^\/+/, "")}`;
}
