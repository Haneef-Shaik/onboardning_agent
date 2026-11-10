/**
 * The app's only way to the API: the generated client from `@onb/ts-api-client` (PRD §11).
 * `createAppClient` adds the session's bearer token and ends the session when the API answers 401
 * to a signed-in call.
 */
import { createApiClient, unwrap, type ApiClient, type Health, type Me } from "@onb/ts-api-client";
import type { Session, SessionStore } from "./auth/session";

export interface AppClientOptions {
  session: SessionStore;
  /** Called after a signed-in call got 401: the token expired or the user was removed. */
  onUnauthenticated: () => void;
  baseUrl?: string;
  fetch?: (request: Request) => Promise<Response>;
}

const AUTHORIZATION = "Authorization";

export function createAppClient({ session, onUnauthenticated, baseUrl, fetch }: AppClientOptions): ApiClient {
  const client = createApiClient({
    ...(baseUrl === undefined ? {} : { baseUrl }),
    ...(fetch === undefined ? {} : { fetch }),
  });
  client.use({
    onRequest({ request }) {
      const current = session.get();
      if (current !== null) {
        request.headers.set(AUTHORIZATION, `Bearer ${current.accessToken}`);
      }
      return request;
    },
    onResponse({ request, response }) {
      if (response.status === 401 && request.headers.has(AUTHORIZATION)) {
        session.clear();
        onUnauthenticated();
      }
      return response;
    },
  });
  return client;
}

export async function fetchHealth(client: ApiClient): Promise<Health> {
  return unwrap(await client.GET("/health"));
}

export async function login(client: ApiClient, email: string, password: string): Promise<Session> {
  const token = unwrap(await client.POST("/auth/login", { body: { email, password } }));
  return { accessToken: token.access_token, expiresAt: token.expires_at };
}

export async function fetchMe(client: ApiClient): Promise<Me> {
  return unwrap(await client.GET("/me"));
}
