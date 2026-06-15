/**
 * Base API client for Test Manager Backend
 * Provides apiGet, apiPost, apiPut, apiDelete functions with authentication
 */

import { STANDALONE_TOKEN_KEY } from "../contexts/quix-auth-context";

// API Error class for consistent error handling
export class ApiError extends Error {
  status: number;
  data: any;

  constructor(message: string, status: number, data?: any) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.data = data;
  }
}

/**
 * Get the API base URL
 * Client-side: Always use relative URLs (proxied by Next.js rewrites)
 * Server-side: Use API_URL environment variable for internal service communication
 */
export function getApiUrl(): string {
  // Client-side: Always use relative URLs for Next.js proxy
  if (typeof window !== "undefined") {
    return ""; // Empty string = relative URLs, proxied by Next.js rewrites
  }

  // Server-side: Use internal service URL
  if (process.env.API_URL) {
    return process.env.API_URL;
  }

  // Fallback for local development (server-side only)
  return "http://localhost:8080";
}

/**
 * Get authentication token from Quix Auth Context
 * Token should be passed as parameter from hooks/components
 * Fallback to localStorage and environment variable for standalone mode
 */
function getAuthToken(providedToken?: string | null): string | null {
  // Use provided token from Quix Auth Context (preferred)
  if (providedToken) {
    return providedToken;
  }

  // Only consult localStorage when running standalone (top-level tab).
  // In embedded mode (iframe), the Portal supplies the token via postMessage
  // and using a stale localStorage PAT would cause spurious 403s.
  if (typeof window !== "undefined" && window === window.parent) {
    const stored = localStorage.getItem(STANDALONE_TOKEN_KEY);
    if (stored) {
      console.log(
        "[API Client] Using token from localStorage (standalone mode)",
      );
      return stored;
    }
  }

  // Fallback: Environment variable (for local development only)
  const envToken = process.env.NEXT_PUBLIC_QUIX_AUTH_TOKEN || null;
  if (envToken) {
    console.log(
      "[API Client] Using token from environment (local development)",
    );
  } else {
    console.warn("[API Client] No auth token available!");
  }

  return envToken;
}

/**
 * Build fetch headers with authentication
 */
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

/**
 * Handle API response
 * Throws ApiError for non-2xx responses
 */
async function handleResponse<T>(response: Response): Promise<T> {
  // Handle 204 No Content
  if (response.status === 204) {
    return undefined as T;
  }

  const contentType = response.headers.get("content-type");
  const isJson = contentType?.includes("application/json");

  if (!response.ok) {
    let errorData: any;
    let message: string;

    if (isJson) {
      errorData = await response.json();
      message =
        errorData?.detail ||
        errorData?.message ||
        `Request failed with status ${response.status}`;
    } else {
      // Non-JSON response (HTML, text, etc.)
      const text = await response.text();
      const isHtml = contentType?.includes("text/html");

      if (isHtml) {
        // Don't show raw HTML to user - provide clean error message
        message = `Request failed with status ${response.status}`;
        errorData = { html: text }; // Store HTML for debugging but don't display it
      } else {
        // Plain text error
        message = text || `Request failed with status ${response.status}`;
        errorData = text;
      }
    }

    console.error(
      "[API Client] Error:",
      message,
      "Status:",
      response.status,
      "Data:",
      errorData,
    );
    throw new ApiError(message, response.status, errorData);
  }

  if (isJson) {
    return response.json();
  }

  return response.text() as any;
}

/**
 * Execute fetch with automatic retry on auth errors (401/403)
 * If request fails with 401/403, attempts to refresh token and retry once
 */
async function fetchWithRetry<T>(
  fetchFn: (token?: string | null) => Promise<Response>,
  currentToken?: string | null,
  refreshTokenFn?: () => Promise<string | null>,
  handler: (response: Response) => Promise<T> = handleResponse,
): Promise<T> {
  try {
    // First attempt with current token
    const response = await fetchFn(currentToken);
    return await handler(response);
  } catch (error) {
    // If auth error and refresh function provided, attempt refresh and retry
    if (
      error instanceof ApiError &&
      (error.status === 401 || error.status === 403) &&
      refreshTokenFn
    ) {
      console.log(
        "[API Client] Auth error detected, attempting token refresh...",
      );

      const freshToken = await refreshTokenFn();

      if (freshToken && freshToken !== currentToken) {
        console.log("[API Client] Got fresh token, retrying request...");
        const retryResponse = await fetchFn(freshToken);
        return await handler(retryResponse);
      } else {
        console.warn("[API Client] Token refresh did not provide new token");
      }
    }

    // Re-throw original error if retry not applicable or retry also failed
    throw error;
  }
}

/**
 * Build URL with query parameters
 */
function buildUrl(endpoint: string, params?: Record<string, any>): string {
  const baseUrl = getApiUrl();
  // Prepend /api/v1 to all endpoints
  const apiEndpoint = endpoint.startsWith("/api/")
    ? endpoint
    : `/api/v1${endpoint}`;

  // For localhost (empty baseUrl), use relative URL
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

/**
 * GET request
 * @param endpoint API endpoint path
 * @param params Query parameters
 * @param token Auth token
 * @param refreshToken Optional function to refresh expired token
 */
export async function apiGet<T>(
  endpoint: string,
  params?: Record<string, any>,
  token?: string | null,
  refreshToken?: () => Promise<string | null>,
): Promise<T> {
  const url = buildUrl(endpoint, params);
  console.log("[API Client] GET", url);

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

/**
 * Handle a binary (Blob) response. Mirrors handleResponse's error handling but
 * returns the body as a Blob instead of parsing JSON.
 */
async function handleBlobResponse(response: Response): Promise<Blob> {
  if (!response.ok) {
    let message = `Request failed with status ${response.status}`;
    try {
      const errorData = await response.json();
      message = errorData?.detail || errorData?.message || message;
    } catch (e) {
      console.debug("[API Client] blob error body was not JSON", e);
    }
    throw new ApiError(message, response.status);
  }
  return response.blob();
}

/**
 * GET request returning a binary Blob (e.g. a PDF), with the same auth +
 * token-refresh behaviour as apiGet.
 */
export async function apiGetBlob(
  endpoint: string,
  token?: string | null,
  refreshToken?: () => Promise<string | null>,
): Promise<Blob> {
  const url = buildUrl(endpoint);
  console.log("[API Client] GET (blob)", url);

  return fetchWithRetry<Blob>(
    (authToken) =>
      fetch(url, {
        method: "GET",
        headers: buildHeaders(authToken),
      }),
    token,
    refreshToken,
    handleBlobResponse,
  );
}

/**
 * POST request
 * @param endpoint API endpoint path
 * @param data Request body data
 * @param token Auth token
 * @param refreshToken Optional function to refresh expired token
 */
export async function apiPost<T>(
  endpoint: string,
  data?: any,
  token?: string | null,
  refreshToken?: () => Promise<string | null>,
): Promise<T> {
  const url = buildUrl(endpoint);

  return fetchWithRetry<T>(
    (authToken) =>
      fetch(url, {
        method: "POST",
        headers: buildHeaders(authToken),
        body: data ? JSON.stringify(data) : undefined,
      }),
    token,
    refreshToken,
  );
}

/**
 * PUT request
 * @param endpoint API endpoint path
 * @param data Request body data
 * @param token Auth token
 * @param refreshToken Optional function to refresh expired token
 */
export async function apiPut<T>(
  endpoint: string,
  data?: any,
  token?: string | null,
  refreshToken?: () => Promise<string | null>,
): Promise<T> {
  const url = buildUrl(endpoint);

  return fetchWithRetry<T>(
    (authToken) =>
      fetch(url, {
        method: "PUT",
        headers: buildHeaders(authToken),
        body: data ? JSON.stringify(data) : undefined,
      }),
    token,
    refreshToken,
  );
}

/**
 * DELETE request
 * @param endpoint API endpoint path
 * @param token Auth token
 * @param refreshToken Optional function to refresh expired token
 */
export async function apiDelete<T = void>(
  endpoint: string,
  token?: string | null,
  refreshToken?: () => Promise<string | null>,
): Promise<T> {
  const url = buildUrl(endpoint);

  return fetchWithRetry<T>(
    (authToken) =>
      fetch(url, {
        method: "DELETE",
        headers: buildHeaders(authToken),
      }),
    token,
    refreshToken,
  );
}
