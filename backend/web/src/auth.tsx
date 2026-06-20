import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { api, clearToken, getToken, setToken } from "./api";
import type { Me } from "./types";

type AuthCtx = {
  token: string | null;
  me: Me | null;
  isAdmin: boolean;
  login: (t: string) => void;
  logout: () => void;
};

const Ctx = createContext<AuthCtx>({
  token: null,
  me: null,
  isAdmin: false,
  login: () => {},
  logout: () => {},
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setTok] = useState<string | null>(getToken());
  const [me, setMe] = useState<Me | null>(null);

  // Load (or refresh) the current user whenever we have a token.
  useEffect(() => {
    if (!token) {
      setMe(null);
      return;
    }
    let alive = true;
    api
      .me()
      .then((m) => alive && setMe(m))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [token]);

  const login = (t: string) => {
    setToken(t);
    setTok(t);
  };
  const logout = () => {
    clearToken();
    setTok(null);
    setMe(null);
  };

  return (
    <Ctx.Provider value={{ token, me, isAdmin: me?.role === "admin", login, logout }}>
      {children}
    </Ctx.Provider>
  );
}

export const useAuth = () => useContext(Ctx);

export function RequireAuth({ children }: { children: ReactNode }) {
  const { token } = useAuth();
  const loc = useLocation();
  if (!token) return <Navigate to="/login" replace state={{ from: loc }} />;
  return <>{children}</>;
}
