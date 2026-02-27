import { useState } from 'react';
import { NavLink } from 'react-router-dom';
import { appRoutes } from '../config/routes';
import { ChevronLeftIcon, ChevronRightIcon, RouteIcon } from './RouteIcon';

export function Sidebar() {
  const [collapsed, setCollapsed] = useState(false);
  const [logoMissing, setLogoMissing] = useState(false);

  return (
    <aside className={`sidebar-surface ${collapsed ? 'is-collapsed' : ''}`}>
      <div className={`sidebar-header ${collapsed ? 'is-collapsed' : ''}`}>
        <NavLink to="/" className="brand-link" aria-label="Go to Home">
          {logoMissing ? (
            <span className="brand-fallback" aria-hidden="true">
              D
            </span>
          ) : (
            <img
              src="/domero_logo_nbg.png"
              alt="Dormero logo"
              className="brand-logo"
              onError={() => setLogoMissing(true)}
            />
          )}
          {!collapsed && (
            <div className="brand-copy">
              <h1 className="sidebar-title">Viktoria Alpha</h1>
              <p className="sidebar-subtitle">Control Center</p>
            </div>
          )}
        </NavLink>

        <button
          type="button"
          className="icon-button sidebar-toggle"
          onClick={() => setCollapsed((prev) => !prev)}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? <ChevronRightIcon className="icon-16" /> : <ChevronLeftIcon className="icon-16" />}
        </button>
      </div>

      {!collapsed && <p className="sidebar-section">Navigation</p>}

      <nav className="nav-list" aria-label="Primary">
        {appRoutes.map((route) => (
          <NavLink
            key={route.id}
            to={route.path}
            className={({ isActive }) => `nav-link ${collapsed ? 'is-icon-only' : ''} ${isActive ? 'is-active' : ''}`}
            title={route.label}
            aria-label={route.label}
          >
            <span className="nav-icon" aria-hidden="true">
              <RouteIcon name={route.icon} className="icon-18" />
            </span>
            {!collapsed && <span className="nav-link-title">{route.label}</span>}
          </NavLink>
        ))}
      </nav>
    </aside>
  );
}
