import { html, nothing } from "lit";
import { togglePasswordVisibility } from "./auth-password-toggle.ts";

export type SignupProps = {
  error: string | null;
  loading: boolean;
  onSignup: (username: string, email: string, password: string, displayName: string) => void;
  onSwitchToLogin: () => void;
};

export function renderSignup(props: SignupProps) {
  const { error, loading, onSignup, onSwitchToLogin } = props;

  const handleSubmit = (e: Event) => {
    e.preventDefault();
    const form = e.target as HTMLFormElement;
    const formData = new FormData(form);
    const username = (formData.get("username") as string).trim();
    const email = (formData.get("email") as string).trim();
    const password = formData.get("password") as string;
    const confirm = formData.get("confirm") as string;
    const displayName = (formData.get("displayName") as string).trim();

    if (password !== confirm) {
      const confirmInput = form.querySelector<HTMLInputElement>('input[name="confirm"]');
      confirmInput?.setCustomValidity("Passwords do not match");
      confirmInput?.reportValidity();
      return;
    }

    if (username && email && password && displayName) {
      onSignup(username, email, password, displayName);
    }
  };

  const clearConfirmValidity = (e: Event) => {
    (e.target as HTMLInputElement).setCustomValidity("");
  };

  return html`
    <div class="auth-page">
      <div class="auth-card">
        <h1 class="auth-title">Create an account</h1>
        ${error ? html`<div class="auth-error">${error}</div>` : nothing}
        <form class="auth-form" @submit=${handleSubmit}>
          <label class="auth-label">
            <span>Username</span>
            <input
              type="text"
              name="username"
              class="auth-input"
              required
              autocomplete="username"
              minlength="3"
              maxlength="32"
              pattern="[a-zA-Z0-9_-]+"
              title="Letters, numbers, hyphens, and underscores only"
              ?disabled=${loading}
            />
          </label>
          <label class="auth-label">
            <span>Display name</span>
            <input
              type="text"
              name="displayName"
              class="auth-input"
              required
              autocomplete="name"
              ?disabled=${loading}
            />
          </label>
          <label class="auth-label">
            <span>Email</span>
            <input
              type="email"
              name="email"
              class="auth-input"
              required
              autocomplete="email"
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
                autocomplete="new-password"
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
          <div class="auth-label">
            <span>Confirm password</span>
            <div class="auth-password-wrap">
              <input
                type="password"
                name="confirm"
                class="auth-input"
                required
                autocomplete="new-password"
                minlength="8"
                @input=${clearConfirmValidity}
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
            ${loading ? "Creating account..." : "Sign up"}
          </button>
        </form>
        <p class="auth-switch">
          Already have an account?
          <a href="#" @click=${(e: Event) => {
            e.preventDefault();
            onSwitchToLogin();
          }}>
            Sign in
          </a>
        </p>
      </div>
    </div>
  `;
}
