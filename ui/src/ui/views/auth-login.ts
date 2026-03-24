import { html, nothing } from "lit";
import { togglePasswordVisibility } from "./auth-password-toggle.ts";

export type LoginProps = {
  error: string | null;
  loading: boolean;
  onLogin: (username: string, password: string) => void;
  onSwitchToSignup: () => void;
};

export function renderLogin(props: LoginProps) {
  const { error, loading, onLogin, onSwitchToSignup } = props;

  const handleSubmit = (e: Event) => {
    e.preventDefault();
    const form = e.target as HTMLFormElement;
    const formData = new FormData(form);
    const username = (formData.get("username") as string).trim();
    const password = formData.get("password") as string;
    if (username && password) {
      onLogin(username, password);
    }
  };

  return html`
    <div class="auth-page">
      <div class="auth-card">
        <h1 class="auth-title">Sign in to OpenClaw</h1>
        ${error ? html`<div class="auth-error">${error}</div>` : nothing}
        <form class="auth-form" @submit=${handleSubmit}>
          <label class="auth-label">
            <span>Username or email</span>
            <input
              type="text"
              name="username"
              class="auth-input"
              required
              autocomplete="username"
              ?disabled=${loading}
            />
          </label>
          <div class="auth-label">
            <span>Password</span>
            <div class="auth-password-wrap">
              <input
                type="password"
                name="password"
                class="auth-input"
                required
                autocomplete="current-password"
                minlength="8"
                ?disabled=${loading}
              />
              <button
                type="button"
                class="auth-password-toggle"
                @click=${togglePasswordVisibility}
                aria-label="Toggle password visibility"
              >
                <svg class="auth-eye-icon auth-eye-open" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
                <svg class="auth-eye-icon auth-eye-closed" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display:none"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>
              </button>
            </div>
          </div>
          <button type="submit" class="auth-button" ?disabled=${loading}>
            ${loading ? "Signing in..." : "Sign in"}
          </button>
        </form>
        <p class="auth-switch">
          Don't have an account?
          <a href="#" @click=${(e: Event) => {
            e.preventDefault();
            onSwitchToSignup();
          }}>
            Sign up
          </a>
        </p>
      </div>
    </div>
  `;
}
