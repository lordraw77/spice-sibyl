import { Component, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router, RouterLink, RouterLinkActive } from '@angular/router';
import { ThemeService } from '../core/services/theme.service';
import { AuthService } from '../core/services/auth.service';

@Component({
  selector: 'app-navbar',
  standalone: true,
  imports: [CommonModule, RouterLink, RouterLinkActive],
  template: `
    <nav class="navbar">
      <div class="brand">
        <span class="brand-name">SpiceSibyl</span>
        <span class="brand-tag">One gateway, many minds.</span>
      </div>
      <button class="nav-toggle" (click)="toggleMobileMenu()" [attr.aria-expanded]="mobileMenuOpen()" aria-label="Apri menu di navigazione">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
      </button>
      <ul class="nav-links" [class.open]="mobileMenuOpen()" (click)="closeMobileMenu()">
        <li>
          <a routerLink="/chat" routerLinkActive="active" ariaCurrentWhenActive="page">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
            Chat
          </a>
        </li>
        <li>
          <a routerLink="/discovery" routerLinkActive="active" ariaCurrentWhenActive="page">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
            Discovery
          </a>
        </li>
        <li>
          <a routerLink="/providers" routerLinkActive="active" ariaCurrentWhenActive="page">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
            Providers
          </a>
        </li>
        <li>
          <a routerLink="/stats" routerLinkActive="active" ariaCurrentWhenActive="page">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>
            Stats
          </a>
        </li>
        <li>
          <a routerLink="/compare" routerLinkActive="active" ariaCurrentWhenActive="page">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="3" width="8" height="18" rx="1"/><rect x="14" y="3" width="8" height="18" rx="1"/></svg>
            Compare
          </a>
        </li>
        <li *ngIf="auth.hasRole('admin')">
          <a routerLink="/ops" routerLinkActive="active" ariaCurrentWhenActive="page">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/><circle cx="12" cy="12" r="4"/></svg>
            Ops
          </a>
        </li>
        <li *ngIf="auth.hasRole('admin')">
          <a routerLink="/mcp" routerLinkActive="active" ariaCurrentWhenActive="page">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><path d="M6.5 10v4.5a2 2 0 0 0 2 2H10"/><path d="M17.5 10v4"/></svg>
            MCP
          </a>
        </li>
      </ul>
      <div class="navbar-actions">
        <div class="accent-picker-wrapper">
          <button class="accent-toggle" (click)="toggleAccentPicker()" [title]="'Colore accento'" [style.background]="themeService.accentColor()">
          </button>
          <div class="accent-popover" *ngIf="accentPickerOpen()">
            <div class="accent-swatches">
              <button
                *ngFor="let c of accentPresets"
                class="accent-swatch"
                [style.background]="c.color"
                [class.active]="themeService.accentColor() === c.color"
                (click)="setAccent(c.color)"
                [title]="c.label"
              ></button>
            </div>
            <div class="accent-custom-row">
              <input type="color" class="accent-color-input" [value]="themeService.accentColor()" (input)="setAccent($any($event.target).value)" title="Colore personalizzato" />
              <button class="accent-reset" *ngIf="!themeService.isDefaultAccent" (click)="themeService.resetAccent()">Reset</button>
            </div>
          </div>
        </div>
        <button class="theme-toggle" (click)="themeService.cycle()" [title]="'Tema: ' + themeService.mode()">
          <svg *ngIf="themeService.resolvedTheme === 'dark'" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>
          <svg *ngIf="themeService.resolvedTheme === 'light'" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>
        </button>
        <div class="user-chip" *ngIf="auth.currentUser() as user">
          <span class="user-email" [title]="user.role">{{ user.email }}</span>
          <button class="logout-btn" (click)="logout()" title="Esci">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
          </button>
        </div>
      </div>
    </nav>
  `,
  styles: [`
    .navbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: .75rem 2rem;
      background: var(--bg-navbar);
      border-bottom: 1px solid var(--border);
      position: sticky;
      top: 0;
      z-index: 100;
    }
    .brand { display: flex; flex-direction: column; gap: .1rem; }
    .brand-name { font-size: 1.1rem; font-weight: 700; color: var(--text-primary); }
    .brand-tag { font-size: .72rem; color: var(--accent); }
    .nav-links {
      display: flex;
      list-style: none;
      margin: 0;
      padding: 0;
      gap: .25rem;
    }
    .nav-toggle {
      display: none;
      align-items: center;
      justify-content: center;
      width: 2.5rem;
      height: 2.5rem;
      border-radius: .5rem;
      border: 1px solid var(--border);
      background: transparent;
      color: var(--text-tertiary);
      cursor: pointer;
      transition: background .15s, color .15s;
    }
    .nav-toggle:hover { background: var(--bg-surface-hover); color: var(--text-primary); }
    .nav-links a {
      display: flex;
      align-items: center;
      gap: .45rem;
      padding: .45rem 1rem;
      border-radius: .55rem;
      text-decoration: none;
      color: var(--text-tertiary);
      font-size: .9rem;
      font-weight: 500;
      transition: background .15s, color .15s;
    }
    .nav-links a:hover { background: var(--bg-surface-hover); color: var(--text-primary); }
    .nav-links a.active {
      background: var(--accent-bg);
      color: var(--accent);
    }
    .navbar-actions {
      display: flex;
      align-items: center;
      gap: .4rem;
    }
    .theme-toggle {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 2rem;
      height: 2rem;
      border-radius: .5rem;
      border: 1px solid var(--border);
      background: transparent;
      color: var(--text-muted);
      cursor: pointer;
      transition: background .15s, color .15s;
    }
    .theme-toggle:hover { background: var(--bg-surface-hover); color: var(--text-primary); }

    /* User chip + logout */
    .user-chip {
      display: flex;
      align-items: center;
      gap: .4rem;
      padding-left: .5rem;
      margin-left: .25rem;
      border-left: 1px solid var(--border);
    }
    .user-email {
      max-width: 160px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-size: .82rem;
      color: var(--text-tertiary);
    }
    .logout-btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 2rem;
      height: 2rem;
      border-radius: .5rem;
      border: 1px solid var(--border);
      background: transparent;
      color: var(--text-muted);
      cursor: pointer;
      transition: background .15s, color .15s;
    }
    .logout-btn:hover { background: var(--bg-surface-hover); color: var(--text-primary); }

    /* Accent picker */
    .accent-picker-wrapper { position: relative; }
    .accent-toggle {
      width: 1.4rem;
      height: 1.4rem;
      border-radius: 50%;
      border: 2px solid var(--border-light);
      cursor: pointer;
      transition: border-color .15s, transform .15s;
    }
    .accent-toggle:hover { border-color: var(--text-muted); transform: scale(1.1); }
    .accent-popover {
      position: absolute;
      top: 2.2rem;
      right: 0;
      background: var(--bg-surface);
      backdrop-filter: blur(12px);
      border: 1px solid var(--border-light);
      border-radius: .65rem;
      padding: .6rem;
      z-index: 200;
      min-width: 180px;
      box-shadow: 0 4px 16px var(--shadow);
    }
    .accent-swatches {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: .35rem;
      margin-bottom: .5rem;
    }
    .accent-swatch {
      width: 1.6rem;
      height: 1.6rem;
      border-radius: 50%;
      border: 2px solid transparent;
      cursor: pointer;
      transition: border-color .15s, transform .15s;
    }
    .accent-swatch:hover { transform: scale(1.15); }
    .accent-swatch.active { border-color: var(--text-primary); }
    .accent-custom-row {
      display: flex;
      align-items: center;
      gap: .4rem;
      padding-top: .4rem;
      border-top: 1px solid var(--border);
    }
    .accent-color-input {
      width: 2rem;
      height: 1.6rem;
      border: 1px solid var(--border);
      border-radius: .3rem;
      cursor: pointer;
      background: transparent;
      padding: 0;
    }
    .accent-color-input::-webkit-color-swatch-wrapper { padding: 1px; }
    .accent-color-input::-webkit-color-swatch { border: none; border-radius: .2rem; }
    .accent-reset {
      font-size: .72rem;
      padding: .15rem .45rem;
      border-radius: .3rem;
      border: 1px solid var(--border);
      background: transparent;
      color: var(--text-muted);
      cursor: pointer;
      transition: background .15s, color .15s;
    }
    .accent-reset:hover { background: var(--bg-surface-hover); color: var(--text-primary); }

    @media (max-width: 575.98px) {
      .navbar { padding: .6rem 1rem; }
      .brand-tag { display: none; }
      .nav-toggle { display: inline-flex; }
      /* Nav links collapse into a dropdown panel under the navbar */
      .nav-links {
        position: absolute;
        top: 100%;
        left: 0;
        right: 0;
        flex-direction: column;
        gap: .15rem;
        padding: .5rem;
        background: var(--bg-navbar);
        border-bottom: 1px solid var(--border);
        box-shadow: 0 8px 24px var(--shadow);
        display: none;
        z-index: 150;
      }
      .nav-links.open { display: flex; }
      .nav-links a { padding: .65rem .85rem; font-size: .9rem; gap: .55rem; }
    }
  `]
})
export class NavbarComponent {
  readonly themeService = inject(ThemeService);
  readonly auth = inject(AuthService);
  readonly accentPickerOpen = signal(false);
  readonly mobileMenuOpen = signal(false);

  toggleMobileMenu(): void {
    this.mobileMenuOpen.update(v => !v);
  }

  closeMobileMenu(): void {
    this.mobileMenuOpen.set(false);
  }

  logout(): void {
    const done = () => window.location.assign('/login');
    this.auth.logout().subscribe({ next: done, error: done });
  }

  readonly accentPresets = [
    { color: '#d6b279', label: 'Gold (default)' },
    { color: '#6b8acd', label: 'Blue' },
    { color: '#6bcd7b', label: 'Green' },
    { color: '#9b7bcd', label: 'Purple' },
    { color: '#cd6b6b', label: 'Red' },
    { color: '#6bcdc0', label: 'Teal' },
    { color: '#cd8f6b', label: 'Orange' },
    { color: '#cd6ba8', label: 'Pink' },
  ];

  toggleAccentPicker(): void {
    this.accentPickerOpen.update(v => !v);
  }

  setAccent(color: string): void {
    this.themeService.setAccent(color);
  }
}
