/**
 * Base API client for Leaderboard Service
 * Provides apiGet function with authentication
 */

import { STANDALONE_TOKEN_KEY } from "../contexts/quix-auth-context";

// API Error class for consistent error handling
export class ApiError extends Error {
  status: number;
  data: unknown;

  constructor(message: string, status: number, data?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.data = data;
  }
}

/**
 * Get the API base URL.
 * Production (static export served by leaderboard-service): always "" —
 * same-origin relative URLs, no baked public URL.
 * Development (`next dev`): NEXT_PUBLIC_LEADERBOARD_SERVICE_URL may point at
 * a locally running FastAPI (e.g. http://localhost:8082).
 */
export function getApiUrl(): string {
  if (process.env.NODE_ENV === "development") {
    return process.env.NEXT_PUBLIC_LEADERBOARD_SERVICE_URL ?? "";
  }
  return ""; // same-origin, relative URLs
}

function getAuthToken(providedToken?: string | null): string | null {
  if (providedToken) {
    return providedToken;
  }

  if (typeof window !== "undefined" && window === window.parent) {
    const stored = localStorage.getItem(STANDALONE_TOKEN_KEY);
    if (stored) {
      return stored;
    }
  }

  const envToken = process.env.NEXT_PUBLIC_QUIX_AUTH_TOKEN || null;
  return envToken;
}

function buildHeaders(providedToken?: string | null): HeadersInit {
  const headers: HeadersInit = {
    "Content-Type": "application/json",
  };

  const token = getAuthToken(providedToken);
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  return headers;
}

async function handleResponse<T>(response: Response): Promise<T> {
  if (response.status === 204) {
    return undefined as T;
  }

  const contentType = response.headers.get("content-type");
  const isJson = contentType?.includes("application/json");

  if (!response.ok) {
    let errorData: unknown;
    let message: string;

    if (isJson) {
      errorData = await response.json();
      message =
        (errorData as Record<string, string>)?.detail ||
        (errorData as Record<string, string>)?.message ||
        `Request failed with status ${response.status}`;
    } else {
      const text = await response.text();
      message = text || `Request failed with status ${response.status}`;
      errorData = text;
    }

    throw new ApiError(message, response.status, errorData);
  }

  if (isJson) {
    return response.json();
  }

  return response.text() as unknown as T;
}

async function fetchWithRetry<T>(
  fetchFn: (token?: string | null) => Promise<Response>,
  currentToken?: string | null,
  refreshTokenFn?: () => Promise<string | null>,
): Promise<T> {
  try {
    const response = await fetchFn(currentToken);
    return await handleResponse<T>(response);
  } catch (error) {
    if (
      error instanceof ApiError &&
      (error.status === 401 || error.status === 403) &&
      refreshTokenFn
    ) {
      const freshToken = await refreshTokenFn();

      if (freshToken && freshToken !== currentToken) {
        const retryResponse = await fetchFn(freshToken);
        return await handleResponse<T>(retryResponse);
      }
    }

    throw error;
  }
}

function buildUrl(endpoint: string, params?: Record<string, unknown>): string {
  const baseUrl = getApiUrl();
  const apiEndpoint = endpoint.startsWith("/api/")
    ? endpoint
    : `/api/v1${endpoint}`;

  let url: URL;
  if (baseUrl === "") {
    url = new URL(apiEndpoint, window.location.origin);
  } else {
    url = new URL(apiEndpoint, baseUrl);
  }

  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== "") {
        url.searchParams.append(key, String(value));
      }
    });
  }

  return url.toString();
}

export async function apiGet<T>(
  endpoint: string,
  params?: Record<string, unknown>,
  token?: string | null,
  refreshToken?: () => Promise<string | null>,
): Promise<T> {
  const url = buildUrl(endpoint, params);

  return fetchWithRetry<T>(
    (authToken) =>
      fetch(url, {
        method: "GET",
        headers: buildHeaders(authToken),
      }),
    token,
    refreshToken,
  );
}
