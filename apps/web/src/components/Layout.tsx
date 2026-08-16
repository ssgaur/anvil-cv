import { Link, NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../store/auth";
import { isAdmin } from "../lib/roles";
import { OnboardingTour } from "./OnboardingTour";

export function Layout() {
  const { user, logout } = useAuth();
  const admin = isAdmin(user?.role);

  return (
    <div className="h-screen flex bg-neutral-950 text-neutral-100">
      {/* Sidebar */}
      <aside className="w-56 bg-neutral-900 border-r border-neutral-800 flex flex-col">
        <Link to="/" className="px-5 py-4 text-lg font-bold tracking-tight border-b border-neutral-800">
          ⚒ Anvil
        </Link>
        <nav className="flex-1 px-2 py-3 space-y-1 text-sm">
          <SideLink to="/projects" label="Projects" icon="📁" />
          <SideLink to="/model" label="Video Editor" icon="◉" />
          <SideLink to="/quality" label="Quality" icon="📊" disabled />
          {admin && <SideLink to="/admin/users" label="Users" icon="👥" />}
        </nav>
        <div className="p-3 border-t border-neutral-800 text-xs text-neutral-400">
          <div className="truncate">{user?.email}</div>
          {user?.role && (
            <div className="text-[10px] uppercase tracking-wider text-neutral-600">{user.role}</div>
          )}
          <button
            onClick={logout}
            className="mt-2 text-neutral-500 hover:text-neutral-200"
          >
            Sign out
          </button>
        </div>
      </aside>

      <main className="flex-1 overflow-auto">
        <Outlet />
      </main>
      <OnboardingTour />
    </div>
  );
}

function SideLink({ to, label, icon, disabled }: { to: string; label: string; icon: string; disabled?: boolean }) {
  if (disabled) {
    return (
      <div className="flex items-center gap-3 px-3 py-2 rounded text-neutral-600 cursor-not-allowed select-none">
        <span>{icon}</span> {label} <span className="ml-auto text-[10px] uppercase tracking-wider">soon</span>
      </div>
    );
  }
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        `flex items-center gap-3 px-3 py-2 rounded text-neutral-300 hover:bg-neutral-800 ${
          isActive ? "bg-neutral-800 text-neutral-100" : ""
        }`
      }
    >
      <span>{icon}</span> {label}
    </NavLink>
  );
}
