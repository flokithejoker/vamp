import { Outlet } from 'react-router-dom';
import { Sidebar } from '../components/Sidebar';

export function AppLayout() {
  return (
    <div className="app-shell">
      <Sidebar />
      <main className="content-area">
        <Outlet />
      </main>
    </div>
  );
}
