import { useCallback, useState } from "react";
import { useAuthStore } from "../stores/auth.ts";
import { useGatewayStore } from "../stores/gateway.ts";
import { loginUser, storeAuthToken } from "../../ui/auth-state.ts";

export default function LoginPage() {
  const authError = useAuthStore((s) => s.authError);
  const authLoading = useAuthStore((s) => s.authLoading);
  const setAuthView = useAuthStore((s) => s.setAuthView);
  const setAuthError = useAuthStore((s) => s.setAuthError);
  const setAuthLoading = useAuthStore((s) => s.setAuthLoading);
  const setAuthState = useAuthStore((s) => s.setAuthState);
  const setAuthUser = useAuthStore((s) => s.setAuthUser);
  const client = useGatewayStore((s) => s.client);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      setAuthError(null);
      if (!username.trim() || !password.trim()) {
        setAuthError("Username and password are required.");
        return;
      }
      setAuthLoading(true);
      try {
        // Use the REST login endpoint
        const basePath = window.location.origin;
        const result = await loginUser(basePath, username, password);
        if ("error" in result) {
          setAuthError(result.error);
          return;
        }
        storeAuthToken(result.token, result.user);
        setAuthUser(result.user);
        setAuthState("authenticated");
      } catch (err) {
        setAuthError(err instanceof Error ? err.message : "Login failed");
      } finally {
        setAuthLoading(false);
      }
    },
    [username, password, setAuthError, setAuthLoading, setAuthState, setAuthUser, client],
  );

  return (
    <div className="auth-page">
      <form className="auth-card" onSubmit={handleSubmit}>
        <h2>Sign in to AI Elevate</h2>

        {authError && (
          <div className="callout danger" style={{ marginBottom: 12 }}>{authError}</div>
        )}

        <label className="auth-field">
          <span>Username</span>
          <input
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            autoFocus
            disabled={authLoading}
          />
        </label>

        <label className="auth-field">
          <span>Password</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            disabled={authLoading}
          />
        </label>

        <button className="btn primary" type="submit" disabled={authLoading} style={{ marginTop: 16, width: "100%" }}>
          {authLoading ? "Signing in..." : "Sign in"}
        </button>

        <div style={{ marginTop: 16, textAlign: "center" }}>
          <span className="muted">No account? </span>
          <button type="button" className="session-link" onClick={() => setAuthView("signup")}>
            Create account
          </button>
        </div>
      </form>
    </div>
  );
}
