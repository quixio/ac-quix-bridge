"use client";

import { useMemo } from "react";
import { useQuixAuth } from "../contexts/quix-auth-context";
import { ApiError } from "../api/client";
import { leaderboardApi as leaderboardApiRaw } from "../api/leaderboard";

function createAuthenticatedApi<
  T extends Record<string, (...args: any[]) => any>,
>(api: T) {
  return function useAuthenticatedApiHook() {
    const { token, refreshToken, clearTokenAndPrompt, isEmbedded, isLoading } =
      useQuixAuth();

    const authenticatedApi = useMemo(() => {
      const apiObj = {} as {
        [K in keyof T]: (
          ...args: Parameters<T[K]> extends [...infer P, any, any]
            ? P
            : Parameters<T[K]>
        ) => ReturnType<T[K]>;
      };

      for (const key in api) {
        const originalFn = api[key];
        // @ts-ignore - Dynamic function wrapping
        apiObj[key] = async (...args: any[]) => {
          try {
            return await originalFn(...args, token, refreshToken);
          } catch (error) {
            if (
              !isLoading &&
              !isEmbedded &&
              error instanceof ApiError &&
              (error.status === 401 || error.status === 403)
            ) {
              clearTokenAndPrompt();
            }
            throw error;
          }
        };
      }

      return apiObj;
    }, [token, refreshToken, clearTokenAndPrompt, isEmbedded, isLoading]);

    return authenticatedApi;
  };
}

export const useLeaderboardApi = createAuthenticatedApi(leaderboardApiRaw);
