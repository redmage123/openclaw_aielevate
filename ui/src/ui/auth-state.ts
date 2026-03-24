import type { AuthUser } from "./app-view-state.ts";

const AUTH_TOKEN_KEY = "openclaw:auth-token";
const AUTH_USER_KEY = "openclaw:auth-user";

export function loadAuthToken(): string | null {
  try {
    return localStorage.getItem(AUTH_TOKEN_KEY);
  } catch {
    return null;
  }
}

export function storeAuthToken(token: string, user: AuthUser): void {
  try {
    localStorage.setItem(AUTH_TOKEN_KEY, token);
    localStorage.setItem(AUTH_USER_KEY, JSON.stringify(user));
  } catch {
    // localStorage may be unavailable
  }
}

export function clearAuthToken(): void {
  try {
    localStorage.removeItem(AUTH_TOKEN_KEY);
    localStorage.removeItem(AUTH_USER_KEY);
  } catch {
    // localStorage may be unavailable
  }
}

export function loadStoredUser(): AuthUser | null {
  try {
    const raw = localStorage.getItem(AUTH_USER_KEY);
    if (!raw) {
      return null;
    }
    return JSON.parse(raw) as AuthUser;
  } catch {
    return null;
  }
}

export async function validateAuthToken(token: string, basePath: string): Promise<AuthUser | null> {
  try {
    const url = `${basePath}/api/auth/me`;
    const res = await fetch(url, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) {
      return null;
    }
    const data = (await res.json()) as { user: AuthUser };
    return data.user;
  } catch {
    return null;
  }
}

export async function loginUser(
  basePath: string,
  username: string,
  password: string,
): Promise<{ token: string; user: AuthUser } | { error: string }> {
  try {
    const res = await fetch(`${basePath}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const data = (await res.json()) as Record<string, unknown>;
    if (!res.ok) {
      const err = data.error as { message?: string } | undefined;
      return { error: err?.message ?? "Login failed" };
    }
    return { token: data.token as string, user: data.user as AuthUser };
  } catch {
    return { error: "Network error" };
  }
}

export async function signupUser(
  basePath: string,
  username: string,
  email: string,
  password: string,
  displayName: string,
): Promise<{ token: string; user: AuthUser } | { error: string }> {
  try {
    const res = await fetch(`${basePath}/api/auth/signup`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, email, password, displayName }),
    });
    const data = (await res.json()) as Record<string, unknown>;
    if (!res.ok) {
      const err = data.error as { message?: string } | undefined;
      return { error: err?.message ?? "Signup failed" };
    }
    return { token: data.token as string, user: data.user as AuthUser };
  } catch {
    return { error: "Network error" };
  }
}

export async function logoutUser(basePath: string, token: string): Promise<void> {
  try {
    await fetch(`${basePath}/api/auth/logout`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
    });
  } catch {
    // Best-effort
  }
  clearAuthToken();
}
