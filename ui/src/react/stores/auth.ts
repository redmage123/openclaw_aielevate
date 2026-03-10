import { create } from "zustand";

export type AuthUser = {
  id: string;
  username: string;
  email: string;
  displayName: string;
  role: string;
};

type AuthState = {
  authState: "checking" | "unauthenticated" | "authenticated" | "not-required";
  authUser: AuthUser | null;
  authView: "login" | "signup";
  authError: string | null;
  authLoading: boolean;
};

type AuthActions = {
  setAuthState: (state: AuthState["authState"]) => void;
  setAuthUser: (user: AuthUser | null) => void;
  setAuthView: (view: "login" | "signup") => void;
  setAuthError: (error: string | null) => void;
  setAuthLoading: (loading: boolean) => void;
  logout: () => void;
  reset: () => void;
};

const defaults: AuthState = {
  authState: "checking",
  authUser: null,
  authView: "login",
  authError: null,
  authLoading: false,
};

export const useAuthStore = create<AuthState & AuthActions>((set) => ({
  ...defaults,

  setAuthState: (authState) => set({ authState }),
  setAuthUser: (user) => set({ authUser: user }),
  setAuthView: (view) => set({ authView: view }),
  setAuthError: (error) => set({ authError: error }),
  setAuthLoading: (loading) => set({ authLoading: loading }),
  logout: () => {
    try { localStorage.removeItem("openclaw_session_token"); } catch {}
    set({ ...defaults, authState: "unauthenticated" });
  },
  reset: () => set({ ...defaults }),
}));

export default useAuthStore;
