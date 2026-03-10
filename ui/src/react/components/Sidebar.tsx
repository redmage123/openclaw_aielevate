import { NavLink } from "react-router-dom";
import { useUiStore } from "../stores/ui.ts";
import { Icon, Icons } from "../icons.tsx";
import { TAB_GROUPS, iconForTab, titleForTab, type Tab } from "../../ui/navigation.ts";

export default function Sidebar() {
  const navCollapsed = useUiStore((s) => s.navCollapsed);
  const navGroupsCollapsed = useUiStore((s) => s.navGroupsCollapsed);
  const toggleNavGroup = useUiStore((s) => s.toggleNavGroup);

  return (
    <nav className={`nav ${navCollapsed ? "nav--collapsed" : ""}`}>
      {/* Organization link — prominent at top */}
      <div className="nav-group">
        <NavLink
          to="/org"
          className={({ isActive }) => `nav-item nav-item--org ${isActive ? "active" : ""}`}
        >
          <span className="nav-item__icon" aria-hidden="true">
            {Icons.globe()}
          </span>
          <span className="nav-item__text">Organization</span>
        </NavLink>
      </div>

      {TAB_GROUPS.map((group) => {
        const isGroupCollapsed = navGroupsCollapsed[group.label] ?? false;

        return (
          <div
            className={`nav-group ${isGroupCollapsed ? "nav-group--collapsed" : ""}`}
            key={group.label}
          >
            <button
              className="nav-label"
              onClick={() => toggleNavGroup(group.label)}
            >
              <span className="nav-label__text">{group.label}</span>
              <span className="nav-label__chevron">&#9662;</span>
            </button>

            <div className="nav-group__items">
              {group.tabs.map((tab) => (
                <NavLink
                  key={tab}
                  to={`/${tab}`}
                  className={({ isActive }) =>
                    `nav-item ${isActive ? "active" : ""}`
                  }
                >
                  <Icon name={iconForTab(tab as Tab)} className="nav-item__icon" />
                  <span className="nav-item__text">{titleForTab(tab as Tab)}</span>
                </NavLink>
              ))}
            </div>
          </div>
        );
      })}
    </nav>
  );
}
