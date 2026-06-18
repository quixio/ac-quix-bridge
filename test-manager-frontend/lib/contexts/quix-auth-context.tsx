"use client";

import React, {
  createContext,
  useContext,
  useEffect,
  useState,
  useCallback,
  useRef,
} from "react";
import { fetchUserProfile } from "@/lib/api/portal-client";

// localStorage key for standalone mode token storage
export const STANDALONE_TOKEN_KEY = "quix_standalone_auth_token";

interface QuixAuthContextType {
  token: string | null;
  isLoading: boolean;
  error: string | null;
  refreshToken: () => Promise<string | null>;
  // Standalone mode additions
  showAuthDialog: boolean;
  authError: string | null;
  handleTokenSubmit: (token: string) => Promise<void>;
  clearTokenAndPrompt: () => void;
  // User profile
  userName: string | null;
  userEmail: string | null;
  isEmbedded: boolean;
}

const QuixAuthContext = createContext<QuixAuthContextType>({
  token: null,
  isLoading: true,
  error: null,
  refreshToken: async () => null,
  showAuthDialog: false,
  authError: null,
  handleTokenSubmit: async () => {},
  clearTokenAndPrompt: () => {},
  userName: null,
  userEmail: null,
  isEmbedded: false,
});

export function useQuixAuth() {
  return useContext(QuixAuthContext);
}

interface QuixAuthProviderProps {
  children: React.ReactNode;
}

export function QuixAuthProvider({ children }: QuixAuthProviderProps) {
  const [token, setTokenRaw] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showAuthDialog, setShowAuthDialog] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);
  const [userName, setUserName] = useState<string | null>(null);
  const [userEmail, setUserEmail] = useState<string | null>(null);
  const isEmbedded = useRef(false);
  const refreshInProgress = useRef(false);

  // Wrap the raw setter so downstream consumers don't see a fresh string
  // reference every time the Portal sends back the same JWT. Without this guard,
  // the periodic 30-minute refresh and the visibilitychange handler
  // both call `setToken(samePayload)` on every tick, which churns the
  // `useEffect([token, …])` dep array and tears down + reopens any
  // WebSocket bound to the token. We use the functional setState form
  // so two near-simultaneous refresh calls can't race past the guard.
  const setToken = useCallback((next: string | null) => {
    setTokenRaw((prev) => (prev === next ? prev : next));
  }, []);

  // Request fresh token from Portal
  const requestTokenFromPortal = useCallback((): Promise<string | null> => {
    return new Promise((resolve) => {
      if (refreshInProgress.current) {
        console.log(
          "[Quix Auth] Token refresh already in progress, waiting...",
        );
        // Wait for existing refresh to complete
        const checkInterval = setInterval(() => {
          if (!refreshInProgress.current) {
            clearInterval(checkInterval);
            resolve(token);
          }
        }, 100);
        setTimeout(() => {
          clearInterval(checkInterval);
          resolve(token);
        }, 5000);
        return;
      }

      refreshInProgress.current = true;
      console.log("[Quix Auth] Requesting fresh token from Portal...");

      const timeoutId = setTimeout(() => {
        refreshInProgress.current = false;
        console.warn("[Quix Auth] Token request timed out");
        resolve(null);
      }, 5000);

      const handleMessage = (event: MessageEvent) => {
        if (event.data && event.data.type === "AUTH_TOKEN") {
          console.log("[Quix Auth] Received fresh token from Portal");
          clearTimeout(timeoutId);
          window.removeEventListener("message", handleMessage);
          refreshInProgress.current = false;

          const newToken = event.data.token;
          setToken(newToken);
          setError(null);
          resolve(newToken);
        }
      };

      window.addEventListener("message", handleMessage);
      window.top!.postMessage(
        { type: "REQUEST_AUTH_TOKEN" },
        "*", // In production, specify exact Portal origin
      );
    });
  }, [token, setToken]);

  // Refresh token function exposed to API client
  const refreshToken = useCallback(async (): Promise<string | null> => {
    if (!isEmbedded.current) {
      // Standalone mode - check localStorage or fallback to env var
      const storedToken = localStorage.getItem(STANDALONE_TOKEN_KEY);
      if (storedToken) {
        console.log("[Quix Auth] Standalone mode: using stored token");
        return storedToken;
      }

      const fallbackToken = process.env.NEXT_PUBLIC_QUIX_AUTH_TOKEN || null;
      if (fallbackToken) {
        console.log(
          "[Quix Auth] Standalone mode: using fallback token from environment",
        );
        return fallbackToken;
      }

      // No token available - will trigger error and show dialog
      console.warn("[Quix Auth] Standalone mode: no token available");
      return null;
    }

    const freshToken = await requestTokenFromPortal();

    if (!freshToken) {
      // Fallback to env var if Portal doesn't respond
      const fallbackToken = process.env.NEXT_PUBLIC_QUIX_AUTH_TOKEN || null;
      if (fallbackToken) {
        console.log("[Quix Auth] Portal didn't respond, using fallback token");
        setToken(fallbackToken);
        return fallbackToken;
      }
    }

    return freshToken;
  }, [requestTokenFromPortal, setToken]);

  // Handle manual token submission in standalone mode
  const handleTokenSubmit = useCallback(
    async (submittedToken: string) => {
      setAuthError(null);

      try {
        // Validate token by making a test API call
        console.log("[Quix Auth] Validating submitted token...");
        const testResponse = await fetch("/api/v1/user/me", {
          headers: {
            Authorization: `Bearer ${submittedToken}`,
          },
        });

        if (testResponse.ok) {
          // Token is valid
          console.log("[Quix Auth] Token validated successfully");
          localStorage.setItem(STANDALONE_TOKEN_KEY, submittedToken);
          setToken(submittedToken);
          setShowAuthDialog(false);
          setAuthError(null);
          setError(null);
        } else {
          // Token is invalid
          const errorMsg = `Token validation failed: ${testResponse.status} ${testResponse.statusText}`;
          console.error("[Quix Auth]", errorMsg);
          setAuthError(errorMsg);
          throw new Error(errorMsg);
        }
      } catch (err) {
        const errorMsg =
          err instanceof Error ? err.message : "Failed to validate token";
        console.error("[Quix Auth] Token validation error:", errorMsg);
        setAuthError(errorMsg);
        throw err;
      }
    },
    [setToken],
  );

  // Clear token and re-prompt for standalone mode (called on 401/403)
  const clearTokenAndPrompt = useCallback(() => {
    console.log("[Quix Auth] Clearing token and prompting for new one");
    localStorage.removeItem(STANDALONE_TOKEN_KEY);
    setToken(null);
    setUserName(null);
    setUserEmail(null);
    setShowAuthDialog(true);
    setAuthError(
      "Your token has expired or is invalid. Please enter a new token.",
    );
  }, [setToken]);

  // Load user profile from Portal API when token is set
  useEffect(() => {
    if (!token) {
      setUserName(null);
      setUserEmail(null);
      return;
    }

    // Fetch user profile from Portal API
    fetchUserProfile(token).then((profile) => {
      if (profile) {
        setUserName(profile.name);
        setUserEmail(profile.email);
      } else {
        // Portal API not available or error - use default
        setUserName("Authenticated User");
        setUserEmail(null);
      }
    });
  }, [token]);

  // Initial token request on mount
  useEffect(() => {
    // Check for local development mode
    const isLocalDev = process.env.NEXT_PUBLIC_LOCAL_DEV_MODE === "true";
    if (isLocalDev) {
      console.log(
        "[Quix Auth] Running in local development mode (no auth required)",
      );
      setToken("local-dev-token"); // Dummy token for local dev
      setUserName("Local Dev User");
      setUserEmail("dev@localhost");
      setIsLoading(false);
      return;
    }

    isEmbedded.current = window !== window.top;

    if (!isEmbedded.current) {
      // Standalone mode - check localStorage first
      console.log("[Quix Auth] Running in standalone mode");

      const storedToken = localStorage.getItem(STANDALONE_TOKEN_KEY);
      if (storedToken) {
        console.log("[Quix Auth] Found stored token in localStorage");
        setToken(storedToken);
        setIsLoading(false);
        return;
      }

      // No stored token - check environment fallback
      const fallbackToken = process.env.NEXT_PUBLIC_QUIX_AUTH_TOKEN || null;
      if (fallbackToken) {
        console.log("[Quix Auth] Using fallback token from environment");
        setToken(fallbackToken);
        setIsLoading(false);
        return;
      }

      // No token available - show auth dialog
      console.log("[Quix Auth] No token available, showing auth dialog");
      setIsLoading(false);
      setShowAuthDialog(true);
      return;
    }

    // Embedded mode - request initial token from Portal
    console.log("[Quix Auth] Running in embedded mode (Quix Portal)");
    requestTokenFromPortal().then((receivedToken) => {
      setIsLoading(false);
      if (!receivedToken) {
        setError("Failed to receive auth token from Quix Portal");

        // Try fallback
        const fallbackToken = process.env.NEXT_PUBLIC_QUIX_AUTH_TOKEN || null;
        if (fallbackToken) {
          console.log("[Quix Auth] Using fallback token from environment");
          setToken(fallbackToken);
          setError(null);
        }
      }
    });
  }, [requestTokenFromPortal, setToken]);

  // Listen for proactive token updates from Portal
  useEffect(() => {
    if (!isEmbedded.current) return;

    const handleProactiveToken = (event: MessageEvent) => {
      // Portal may send new tokens proactively when they're refreshed
      if (
        event.data &&
        event.data.type === "AUTH_TOKEN" &&
        !refreshInProgress.current
      ) {
        console.log("[Quix Auth] Received proactive token update from Portal");
        setToken(event.data.token);
        setError(null);
      }
    };

    window.addEventListener("message", handleProactiveToken);
    return () => window.removeEventListener("message", handleProactiveToken);
  }, [setToken]);

  // Periodic token refresh (every 30 minutes)
  useEffect(() => {
    if (!isEmbedded.current) return;

    console.log(
      "[Quix Auth] Starting periodic token refresh (every 30 minutes)",
    );
    const interval = setInterval(
      () => {
        console.log("[Quix Auth] Periodic token refresh triggered");
        refreshToken();
      },
      30 * 60 * 1000,
    ); // 30 minutes

    return () => clearInterval(interval);
  }, [refreshToken]);

  // Refresh token when user returns to tab
  useEffect(() => {
    if (!isEmbedded.current) return;

    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible" && token) {
        console.log("[Quix Auth] User returned to tab, refreshing token");
        refreshToken();
      }
    };

    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () =>
      document.removeEventListener("visibilitychange", handleVisibilityChange);
  }, [refreshToken, token]);

  return (
    <QuixAuthContext.Provider
      value={{
        token,
        isLoading,
        error,
        refreshToken,
        showAuthDialog,
        authError,
        handleTokenSubmit,
        clearTokenAndPrompt,
        userName,
        userEmail,
        isEmbedded: isEmbedded.current,
      }}
    >
      {children}
    </QuixAuthContext.Provider>
  );
}
