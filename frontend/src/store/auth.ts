import { create } from "zustand";

export type AuthStatus = "checking" | "signed_out" | "no_password" | "signed_in";

interface AuthStore {
  status: AuthStatus;
  set: (status: AuthStatus) => void;
}

export const useAuth = create<AuthStore>((set) => ({
  status: "checking",
  set: (status) => set({ status }),
}));
