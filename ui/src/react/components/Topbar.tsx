import { useCallback, useRef, useState, useEffect } from "react";
import { useGatewayStore } from "../stores/gateway.ts";
import { useUiStore } from "../stores/ui.ts";
import { useAuthStore } from "../stores/auth.ts";
import { Icon, Icons } from "../icons.tsx";
import ThemeToggle from "./ThemeToggle.tsx";

export default function Topbar() {
  const connected = useGatewayStore((s) => s.connected);
  const hello = useGatewayStore((s) => s.hello);
  const toggleNav = useUiStore((s) => s.toggleNav);
  const authUser = useAuthStore((s) => s.authUser);
  const authState = useAuthStore((s) => s.authState);
  const logout = useAuthStore((s) => s.logout);

  const version = hello?.server?.version;

  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  // Close user menu on outside click
  useEffect(() => {
    if (!userMenuOpen) return;
    function handleClick(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setUserMenuOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [userMenuOpen]);

  const handleLogout = useCallback(() => {
    setUserMenuOpen(false);
    logout();
  }, [logout]);

  return (
    <header className="topbar">
      <div className="topbar-left">
        <button
          className="nav-collapse-toggle"
          onClick={toggleNav}
          aria-label="Toggle navigation"
        >
          <span className="nav-collapse-toggle__icon">
            {Icons.menu()}
          </span>
        </button>

        <div className="brand">
          <div className="brand-text">
            <span className="brand-title">AI Elevate</span>
            <span className="brand-sub">Control</span>
          </div>
        </div>
      </div>

      <div className="topbar-center">
        <div className="pill">
          <span className={`statusDot ${connected ? "ok" : ""}`} />
          <span className="mono">{connected ? "Connected" : "Disconnected"}</span>
        </div>
      </div>

      <div className="topbar-status">
        {version && (
          <span className="pill">
            <span className="mono">v{version}</span>
          </span>
        )}

        <ThemeToggle />

        {authState === "authenticated" && authUser && (
          <div className="user-menu" ref={menuRef}>
            <span className="user-menu__icon">
              {Icons.userCircle()}
            </span>
            <span className="user-menu__name">{authUser.displayName || authUser.username}</span>
            <button
              className="user-menu__logout"
              onClick={handleLogout}
              title="Log out"
              aria-label="Log out"
            >
              <span className="user-menu__logout-icon">
                {Icons.logOut()}
              </span>
            </button>
          </div>
        )}
      </div>
    </header>
  );
}
