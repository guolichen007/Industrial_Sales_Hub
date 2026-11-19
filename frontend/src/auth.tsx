import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { ApiError, get, post } from "./api";

export type Me = {
  id: number;
  username: string;
  name: string;
  role: "ADMIN" | "LEADER" | "ASSISTANT" | "SALES" | "FINANCE";
  role_label: string;
  caps: string[];
  flags: { sales_quote_summary: boolean; performance_confirm_mode: string; sales_see_all_projects: boolean };
};

type Ctx = { me: Me | null; loading: boolean; can: (cap: string) => boolean; login: (u: string, p: string) => Promise<void>; logout: () => Promise<void> };
const AuthCtx = createContext<Ctx>(null as unknown as Ctx);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    get<Me>("/auth/me/")
      .then(setMe)
      .catch((e) => {
        if (!(e instanceof ApiError)) console.error(e);
      })
      .finally(() => setLoading(false));
  }, []);
  const login = useCallback(async (username: string, password: string) => {
    setMe(await post<Me>("/auth/login/", { username, password }));
  }, []);
  const logout = useCallback(async () => {
    await post("/auth/logout/");
    setMe(null);
  }, []);
  const can = useCallback((cap: string) => !!me?.caps.includes(cap), [me]);
  return <AuthCtx.Provider value={{ me, loading, can, login, logout }}>{children}</AuthCtx.Provider>;
}

export const useAuth = () => useContext(AuthCtx);
