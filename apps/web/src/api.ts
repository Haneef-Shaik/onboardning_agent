import { createApiClient, unwrap, type ApiClient, type Health } from "@onb/ts-api-client";

/** The app's API client: same origin, under `/api`. */
export const api: ApiClient = createApiClient();

export async function fetchHealth(client: ApiClient = api): Promise<Health> {
  return unwrap(await client.GET("/health"));
}
