import { Injectable, signal } from '@angular/core';

export type ThemeMode = 'dark' | 'light' | 'system';

const STORAGE_KEY = 'spicesibyl_theme';
const ACCENT_KEY = 'spicesibyl_accent';
const DEFAULT_ACCENT = '#d6b279';

@Injectable({ providedIn: 'root' })
export class ThemeService {
  readonly mode = signal<ThemeMode>(this.loadMode());
  readonly accentColor = signal<string>(this.loadAccent());
  private mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');

  constructor() {
    this.applyTheme();
    this.applyAccent();
    this.mediaQuery.addEventListener('change', () => {
      if (this.mode() === 'system') this.applyTheme();
    });
  }

  setMode(mode: ThemeMode): void {
    this.mode.set(mode);
    localStorage.setItem(STORAGE_KEY, mode);
    this.applyTheme();
  }

  cycle(): void {
    const order: ThemeMode[] = ['dark', 'light', 'system'];
    const idx = order.indexOf(this.mode());
    this.setMode(order[(idx + 1) % order.length]);
  }

  get resolvedTheme(): 'dark' | 'light' {
    const m = this.mode();
    if (m === 'system') return this.mediaQuery.matches ? 'dark' : 'light';
    return m;
  }

  setAccent(color: string): void {
    this.accentColor.set(color);
    localStorage.setItem(ACCENT_KEY, color);
    this.applyAccent();
  }

  resetAccent(): void {
    this.accentColor.set(DEFAULT_ACCENT);
    localStorage.removeItem(ACCENT_KEY);
    this.applyAccent();
  }

  get isDefaultAccent(): boolean {
    return this.accentColor() === DEFAULT_ACCENT;
  }

  private loadMode(): ThemeMode {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === 'dark' || saved === 'light' || saved === 'system') return saved;
    return 'dark';
  }

  private loadAccent(): string {
    return localStorage.getItem(ACCENT_KEY) || DEFAULT_ACCENT;
  }

  private applyTheme(): void {
    document.documentElement.setAttribute('data-theme', this.resolvedTheme);
  }

  private applyAccent(): void {
    const hex = this.accentColor();
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    const style = document.documentElement.style;

    style.setProperty('--accent', hex);
    style.setProperty('--accent-bg', `rgba(${r}, ${g}, ${b}, 0.1)`);
    style.setProperty('--accent-border', `rgba(${r}, ${g}, ${b}, 0.2)`);
    style.setProperty('--accent-text', this.lighten(hex, 0.15));
    style.setProperty('--accent-hover', `rgba(${r}, ${g}, ${b}, 0.2)`);
    style.setProperty('--border-focus', `rgba(${r}, ${g}, ${b}, 0.45)`);
  }

  private lighten(hex: string, amount: number): string {
    let r = parseInt(hex.slice(1, 3), 16);
    let g = parseInt(hex.slice(3, 5), 16);
    let b = parseInt(hex.slice(5, 7), 16);
    r = Math.min(255, Math.round(r + (255 - r) * amount));
    g = Math.min(255, Math.round(g + (255 - g) * amount));
    b = Math.min(255, Math.round(b + (255 - b) * amount));
    return `#${r.toString(16).padStart(2, '0')}${g.toString(16).padStart(2, '0')}${b.toString(16).padStart(2, '0')}`;
  }
}
