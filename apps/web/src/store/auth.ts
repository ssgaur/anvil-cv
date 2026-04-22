import { create } from "zustand";
import { api } from "../lib/api";
import type { User } from "../types";

interface AuthState {
  user: User | null;
  loading: boolean;
  hydrate: () => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  signup: (email: string, name: string, password: string) => Promise<void>;
  logout: () => void;
}

export const useAuth = create<AuthState>((set) => ({
  user: null,
  loading: true,
  hydrate: async () => {
    if (!api.token()) { set({ user: null, loading: false }); return; }
    try {
      const u = await api.me();
      set({ user: u, loading: false });
    } catch {
      set({ user: null, loading: false });
    }
  },
  login: async (email, password) => {
    await api.login(email, password);
    const u = await api.me();
    set({ user: u });
  },
  signup: async (email, name, password) => {
    await api.signup(email, name, password);
    const u = await api.me();
    set({ user: u });
  },
  logout: () => { api.setToken(null); set({ user: null }); },
}));
