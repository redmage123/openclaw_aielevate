import { html, nothing } from "lit";

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
          <label class="auth-label">
            <span>Password</span>
            <input
              type="password"
              name="password"
              class="auth-input"
              required
              autocomplete="new-password"
              minlength="8"
              ?disabled=${loading}
            />
          </label>
          <label class="auth-label">
            <span>Confirm password</span>
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
          </label>
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
