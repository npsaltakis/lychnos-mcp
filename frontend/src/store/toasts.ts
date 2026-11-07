import { create } from "zustand";

export type ToastLevel = "success" | "info" | "warning" | "error";

export interface Toast {
  id: number;
  level: ToastLevel;
  title: string;
  message?: string;
}

interface ToastStore {
  toasts: Toast[];
  push: (toast: Omit<Toast, "id">) => void;
  dismiss: (id: number) => void;
}

let nextId = 1;
const TTL: Record<ToastLevel, number> = { success: 2500, info: 3500, warning: 6000, error: 8000 };

export const useToasts = create<ToastStore>((set, get) => ({
  toasts: [],
  push: (toast) => {
    const id = nextId++;
    // Collapse identical consecutive toasts (e.g. repeated errors while dragging a slider).
    const last = get().toasts.at(-1);
    if (last && last.title === toast.title && last.message === toast.message) return;
    set((s) => ({ toasts: [...s.toasts.slice(-4), { ...toast, id }] }));
    setTimeout(() => get().dismiss(id), TTL[toast.level]);
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));

export const notify = (level: ToastLevel, title: string, message?: string) =>
  useToasts.getState().push({ level, title, message });
