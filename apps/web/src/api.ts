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

const bearer = (session: Session) => `Bearer ${session.accessToken}`;

/** The API's token is already expired by this browser's clock, so it cannot be used. */
export class ClockSkewError extends Error {
  constructor() {
    super("The token is already expired by this device's clock");
    this.name = "ClockSkewError";
  }
}

export function createAppClient({ session, onUnauthenticated, baseUrl, fetch }: AppClientOptions): ApiClient {
  const client = createApiClient({
    ...(baseUrl === undefined ? {} : { baseUrl }),
    ...(fetch === undefined ? {} : { fetch }),
  });
  client.use({
    onRequest({ request }) {
      const current = session.get();
      if (current !== null) {
        request.headers.set(AUTHORIZATION, bearer(current));
      }
      return request;
    },
    onResponse({ request, response }) {
      // Only a 401 for the token in use now: a late answer for an older token must not end a new session.
      const sent = request.headers.get(AUTHORIZATION);
      const current = session.get();
      if (response.status === 401 && sent !== null && current !== null && sent === bearer(current)) {
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
  if (token.expires_at * 1000 <= Date.now()) throw new ClockSkewError();
  return { accessToken: token.access_token, expiresAt: token.expires_at };
}

export async function fetchMe(client: ApiClient): Promise<Me> {
  return unwrap(await client.GET("/me"));
}
