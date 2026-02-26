/** Toggle password field between visible/hidden and swap eye icons. */
export function togglePasswordVisibility(e: Event): void {
  const btn = (e.currentTarget as HTMLElement).closest(".auth-password-toggle") as HTMLElement;
  if (!btn) {
    return;
  }
  const wrap = btn.closest(".auth-password-wrap");
  if (!wrap) {
    return;
  }
  const input = wrap.querySelector("input");
  if (!input) {
    return;
  }
  const showing = input.type === "text";
  input.type = showing ? "password" : "text";
  const openIcon = btn.querySelector<HTMLElement>(".auth-eye-open");
  const closedIcon = btn.querySelector<HTMLElement>(".auth-eye-closed");
  if (openIcon && closedIcon) {
    openIcon.style.display = showing ? "" : "none";
    closedIcon.style.display = showing ? "none" : "";
  }
}
