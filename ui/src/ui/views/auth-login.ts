import { html, nothing } from "lit";

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
          <label class="auth-label">
            <span>Password</span>
            <input
              type="password"
              name="password"
              class="auth-input"
              required
              autocomplete="current-password"
              minlength="8"
              ?disabled=${loading}
            />
          </label>
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
