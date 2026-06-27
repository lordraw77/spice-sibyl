import { Injectable, signal } from '@angular/core';

const STORAGE_KEY = 'spicesibyl_onboarded';

/**
 * Drives the first-run guided tour. A single localStorage flag records whether
 * the user has already completed (or skipped) the tour so it does not reappear.
 * `restart()` lets the user replay it on demand.
 */
@Injectable({ providedIn: 'root' })
export class OnboardingService {
  /** True while the tour overlay should be visible. */
  readonly active = signal<boolean>(false);

  private seen(): boolean {
    try {
      return localStorage.getItem(STORAGE_KEY) === 'true';
    } catch {
      return true; // if storage is unavailable, don't nag the user
    }
  }

  private markSeen(): void {
    try {
      localStorage.setItem(STORAGE_KEY, 'true');
    } catch {
      /* ignore */
    }
  }

  /** Whether the tour has never been completed/skipped yet. */
  get isFirstRun(): boolean {
    return !this.seen();
  }

  /** Start the tour only if it has never been seen (called on first chat load). */
  maybeStart(): void {
    if (this.isFirstRun) {
      this.active.set(true);
    }
  }

  /** Force-start the tour (e.g. from a "replay tour" action). */
  restart(): void {
    this.active.set(true);
  }

  /** User finished the tour. */
  complete(): void {
    this.markSeen();
    this.active.set(false);
  }

  /** User dismissed the tour early. */
  skip(): void {
    this.markSeen();
    this.active.set(false);
  }
}
