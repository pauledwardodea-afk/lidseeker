import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";

export function Layout() {
  const { logout, me } = useAuth();
  const nav = useNavigate();
  const onLogout = () => {
    logout();
    nav("/login");
  };
  return (
    <>
      <header className="appbar">
        <div className="appbar-inner">
          <span className="brand">
            Lidseeker<span className="beta">beta</span>
          </span>
          <nav className="nav">
            <NavLink to="/" end>
              Discover
            </NavLink>
            <NavLink to="/search">Search</NavLink>
            <NavLink to="/requests">Requests</NavLink>
            <NavLink to="/settings">Settings</NavLink>
            {me && <span className="muted" style={{ alignSelf: "center", fontSize: 13, padding: "0 4px" }}>{me.username}</span>}
            <button className="logout" onClick={onLogout}>
              Log out
            </button>
          </nav>
        </div>
      </header>
      <main className="container">
        <Outlet />
      </main>
    </>
  );
}
