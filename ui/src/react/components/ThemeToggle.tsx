import { useCallback } from "react";
import { useUiStore } from "../stores/ui.ts";
import { Icons } from "../icons.tsx";

const MODES = ["system", "light", "dark"] as const;

export default function ThemeToggle() {
  const theme = useUiStore((s) => s.theme);
  const cycleTheme = useUiStore((s) => s.cycleTheme);

  const themeIndex = MODES.indexOf(theme);

  const handleClick = useCallback(
    (mode: (typeof MODES)[number]) => {
      const store = useUiStore.getState();
      store.setTheme(mode);
    },
    [],
  );

  return (
    <div
      className="theme-toggle"
      style={{ "--theme-index": themeIndex } as React.CSSProperties}
    >
      <div className="theme-toggle__track">
        <span
          className="theme-toggle__indicator"
          style={{ "--theme-index": themeIndex } as React.CSSProperties}
        />

        <button
          className={`theme-toggle__button ${theme === "system" ? "active" : ""}`}
          onClick={() => handleClick("system")}
          title="System theme"
          aria-label="System theme"
        >
          <svg className="theme-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="10" />
            <path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20" />
            <path d="M2 12h20" />
          </svg>
        </button>

        <button
          className={`theme-toggle__button ${theme === "light" ? "active" : ""}`}
          onClick={() => handleClick("light")}
          title="Light theme"
          aria-label="Light theme"
        >
          <svg className="theme-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="4" />
            <path d="M12 2v2" /><path d="M12 20v2" />
            <path d="m4.93 4.93 1.41 1.41" /><path d="m17.66 17.66 1.41 1.41" />
            <path d="M2 12h2" /><path d="M20 12h2" />
            <path d="m6.34 17.66-1.41 1.41" /><path d="m19.07 4.93-1.41 1.41" />
          </svg>
        </button>

        <button
          className={`theme-toggle__button ${theme === "dark" ? "active" : ""}`}
          onClick={() => handleClick("dark")}
          title="Dark theme"
          aria-label="Dark theme"
        >
          <svg className="theme-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z" />
          </svg>
        </button>
      </div>
    </div>
  );
}
