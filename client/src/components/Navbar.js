import React from 'react';
import { Link, NavLink } from 'react-router-dom';
import { Activity, LogOut } from 'lucide-react';
import { useAuth } from '../contexts/AuthContext';

const linkClass = ({ isActive }) =>
  `rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${isActive ? 'bg-gray-100 text-gray-900' : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'}`;

const Navbar = () => {
  const { user, logout } = useAuth();
  const isDoctor = user?.role === 'doctor';

  return (
    <nav className="border-b border-gray-200 bg-white">
      <div className="mx-auto flex h-14 max-w-7xl items-center justify-between px-4 sm:px-6">
        <div className="flex items-center gap-3 sm:gap-6">
          <Link to="/" className="flex items-center gap-2 text-gray-900">
            <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-blue-600 text-white">
              <Activity className="h-4 w-4" />
            </span>
            <span className="text-base font-semibold">Eir</span>
          </Link>
          {isDoctor && (
            <div className="flex items-center gap-1">
              <NavLink to="/" end className={linkClass}>Cases</NavLink>
              <NavLink to="/settings" className={linkClass}>Settings</NavLink>
            </div>
          )}
        </div>

        <div className="flex items-center gap-3 text-sm">
          <span className="hidden text-gray-900 sm:inline">{user?.first_name} {user?.last_name}</span>
          <span className="hidden text-gray-500 sm:inline">{user?.role === 'emt' ? 'EMT' : 'Doctor'}</span>
          <button onClick={logout} aria-label="Log out"
            className="flex items-center gap-1.5 rounded-md px-2 py-1.5 text-gray-600 hover:bg-gray-100 hover:text-gray-900">
            <LogOut className="h-4 w-4" />
            <span className="hidden sm:inline">Log out</span>
          </button>
        </div>
      </div>
    </nav>
  );
};

export default Navbar;
