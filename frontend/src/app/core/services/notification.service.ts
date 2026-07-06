import { Injectable, signal } from '@angular/core';

export type ToastType = 'error' | 'warning' | 'info' | 'success';

export interface ToastAction {
  label: string;
  onClick: () => void;
}

export interface Toast {
  id: string;
  type: ToastType;
  title: string;
  detail?: string;
  onClick?: () => void;
  actions?: ToastAction[];
}

@Injectable({ providedIn: 'root' })
export class NotificationService {
  readonly toasts = signal<Toast[]>([]);

  add(
    type: ToastType,
    title: string,
    detail?: string,
    durationMs = 6000,
    onClick?: () => void,
    actions?: ToastAction[],
  ): void {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    this.toasts.update(list => [...list, { id, type, title, detail, onClick, actions }]);
    window.setTimeout(() => this.dismiss(id), durationMs);
  }

  dismiss(id: string): void {
    this.toasts.update(list => list.filter(t => t.id !== id));
  }
}
