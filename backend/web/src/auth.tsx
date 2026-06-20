import { createContext, useContext, useState, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { clearToken, getToken, setToken } from "./api";

type AuthCtx = {
  token: string | null;
  login: (t: string) => void;
  logout: () => void;
};

const Ctx = createContext<AuthCtx>({ token: null, login: () => {}, logout: () => {} });

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setTok] = useState<string | null>(getToken());
  const login = (t: string) => {
    setToken(t);
    setTok(t);
  };
  const logout = () => {
    clearToken();
    setTok(null);
  };
  return <Ctx.Provider value={{ token, login, logout }}>{children}</Ctx.Provider>;
}

export const useAuth = () => useContext(Ctx);

export function RequireAuth({ children }: { children: ReactNode }) {
  const { token } = useAuth();
  const loc = useLocation();
  if (!token) return <Navigate to="/login" replace state={{ from: loc }} />;
  return <>{children}</>;
}
