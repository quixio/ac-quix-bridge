/**
 * User Profile API Client
 * Fetches user profile information via backend (which calls Portal API server-side)
 */

export interface UserProfile {
  user_id: string
  name: string
  email: string
}

/**
 * Fetch user profile from backend API
 * Backend handles Portal API communication server-side
 */
export async function fetchUserProfile(token: string): Promise<UserProfile | null> {
  try {
    console.log("[User API] Fetching user profile...")
    const response = await fetch(`/api/v1/user/me`, {
      method: "GET",
      headers: {
        "Authorization": `Bearer ${token}`,
        "Content-Type": "application/json",
      },
    })

    if (!response.ok) {
      console.error(`[User API] Failed to fetch profile: ${response.status} ${response.statusText}`)
      return null
    }

    const data = await response.json()
    console.log("[User API] User profile fetched successfully")

    return {
      user_id: data.user_id || "unknown",
      name: data.name || "Authenticated User",
      email: data.email || "",
    }
  } catch (error) {
    console.error("[User API] Error fetching user profile:", error)
    return null
  }
}
