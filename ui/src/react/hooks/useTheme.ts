import { useEffect } from "react";
import { resolveTheme } from "../../ui/theme.ts";
import { useUiStore } from "../stores/ui.ts";

/**
 * Manages theme resolution and applies it to the document.
 * Listens to the system prefers-color-scheme media query and updates
 * the resolved theme when the user's preference or system setting changes.
 */
export function useTheme() {
  const theme = useUiStore((s) => s.theme);
  const themeResolved = useUiStore((s) => s.themeResolved);
  const setTheme = useUiStore((s) => s.setTheme);
  const setThemeResolved = useUiStore((s) => s.setThemeResolved);

  useEffect(() => {
    // Apply the current resolved theme to the DOM
    const resolved = resolveTheme(theme);
    setThemeResolved(resolved);

    // Listen for system theme changes (only matters when theme === "system")
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => {
      if (useUiStore.getState().theme === "system") {
        const next = resolveTheme("system");
        setThemeResolved(next);
      }
    };

    mq.addEventListener("change", onChange);
    return () => {
      mq.removeEventListener("change", onChange);
    };
  }, [theme, setThemeResolved]);

  return { theme, themeResolved, setTheme };
}

export default useTheme;
