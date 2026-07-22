import { Component, HostListener, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { Router, RouterLink, RouterLinkActive } from '@angular/router';
import { ThemeService } from '../core/services/theme.service';
import { AuthService } from '../core/services/auth.service';
import { I18nService } from '../core/i18n/i18n.service';
import { TranslatePipe } from '../core/i18n/translate.pipe';
import { Locale } from '../core/i18n/locale';
import { FlagComponent } from '../core/i18n/flag.component';
import { ProfileService } from '../core/services/profile.service';
import { FeatureService } from '../core/services/feature.service';

interface NavItem {
  label: string;
  route?: string;
  icon: string; // inner SVG markup (paths/shapes)
  adminOnly?: boolean;
  feature?: string; // gate on an admin feature toggle (see FeatureService)
  children?: NavItem[];
}

// Reusable inner-SVG snippets (stroked, 24x24 viewBox).
const ICONS = {
  chat: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  models:
    '<polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/>',
  providers:
    '<rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/>',
  discovery: '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
  compare: '<rect x="2" y="3" width="8" height="18" rx="1"/><rect x="14" y="3" width="8" height="18" rx="1"/>',
  stats: '<line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>',
  tools:
    '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
  workflow:
    '<circle cx="5" cy="6" r="3"/><path d="M5 9v6a2 2 0 0 0 2 2h8"/><circle cx="19" cy="17" r="3"/><path d="M19 14V8a2 2 0 0 0-2-2h-4"/>',
  mcp:
    '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><path d="M6.5 10v4.5a2 2 0 0 0 2 2H10"/><path d="M17.5 10v4"/>',
  workspace:
    '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
  resources: '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
  template: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>',
  tag: '<path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/>',
  knowledge:
    '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>',
  memory:
    '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>',
  info: '<circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>',
  help: '<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
  ops:
    '<path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/><circle cx="12" cy="12" r="4"/>',
  reminders:
    '<circle cx="12" cy="13" r="8"/><path d="M12 9v4l2.5 2.5"/><path d="M9 3h6"/>',
  settings:
    '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
};

@Component({
  selector: 'app-navbar',
  standalone: true,
  imports: [CommonModule, RouterLink, RouterLinkActive, TranslatePipe, FlagComponent],
  template: `
    <nav class="navbar">
      <div class="brand">
        <img class="brand-logo" src="logo.png" alt="SpiceSibyl logo" />
        <div class="brand-text">
          <span class="brand-name">SpiceSibyl</span>
          <span class="brand-tag">One gateway, many minds.</span>
        </div>
      </div>
      <button class="nav-toggle" (click)="toggleMobileMenu()" [attr.aria-expanded]="mobileMenuOpen()" [attr.aria-label]="'navbar.menu' | t">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
      </button>
      <ul class="nav-links" [class.open]="mobileMenuOpen()">
        <ng-container *ngFor="let item of menu">
          <li *ngIf="isVisible(item)" class="nav-item" [class.has-children]="!!item.children">
            <!-- Leaf link -->
            <a
              *ngIf="!item.children"
              [routerLink]="item.route"
              routerLinkActive="active"
              ariaCurrentWhenActive="page"
              (click)="onLeafClick()"
            >
              <span class="nav-icon" [innerHTML]="iconHtml(item.icon)"></span>
              {{ item.label | t }}
            </a>

            <!-- Macro-voce with submenu -->
            <ng-container *ngIf="item.children">
              <button
                class="nav-parent"
                [class.active]="isGroupActive(item)"
                [class.open]="openMenu() === item.label"
                [attr.aria-expanded]="openMenu() === item.label"
                (click)="toggleMenu(item.label, $event)"
              >
                <span class="nav-icon" [innerHTML]="iconHtml(item.icon)"></span>
                {{ item.label | t }}
                <svg class="nav-caret" [class.open]="openMenu() === item.label" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
              </button>
              <ul class="nav-submenu" [class.open]="openMenu() === item.label">
                <ng-container *ngFor="let child of (item.children || [])">
                  <li *ngIf="isVisible(child)">
                    <a
                      [routerLink]="child.route"
                      routerLinkActive="active"
                      ariaCurrentWhenActive="page"
                      (click)="onLeafClick()"
                    >
                      <span class="nav-icon" [innerHTML]="iconHtml(child.icon)"></span>
                      {{ child.label | t }}
                    </a>
                  </li>
                </ng-container>
              </ul>
            </ng-container>
          </li>
        </ng-container>
      </ul>
      <div class="navbar-actions">
        <div class="lang-picker-wrapper">
          <button class="lang-toggle" (click)="toggleLangPicker($event)" [title]="'navbar.language' | t" [attr.aria-label]="'navbar.language' | t">
            <app-flag [code]="i18n.locale()" [size]="14" />
            <span class="lang-code">{{ i18n.locale() }}</span>
          </button>
          <div class="lang-popover" *ngIf="langPickerOpen()" (click)="$event.stopPropagation()">
            <button
              *ngFor="let l of i18n.supported"
              class="lang-option"
              [class.active]="i18n.locale() === l.code"
              (click)="setLocale(l.code)"
            ><app-flag [code]="l.code" [size]="15" /><span>{{ l.name }}</span></button>
          </div>
        </div>
        <div class="accent-picker-wrapper">
          <button class="accent-toggle" (click)="toggleAccentPicker($event)" [title]="'navbar.accent' | t" [style.background]="themeService.accentColor()">
          </button>
          <div class="accent-popover" *ngIf="accentPickerOpen()" (click)="$event.stopPropagation()">
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
              <input type="color" class="accent-color-input" [value]="themeService.accentColor()" (input)="setAccent($any($event.target).value)" [title]="'navbar.accentCustom' | t" />
              <button class="accent-reset" *ngIf="!themeService.isDefaultAccent" (click)="themeService.resetAccent()">{{ 'common.reset' | t }}</button>
            </div>
          </div>
        </div>
        <button class="theme-toggle" (click)="themeService.cycle()" [title]="('navbar.theme' | t) + ': ' + themeService.mode()">
          <svg *ngIf="themeService.resolvedTheme === 'dark'" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>
          <svg *ngIf="themeService.resolvedTheme === 'light'" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>
        </button>
        <div class="user-chip" *ngIf="auth.currentUser() as user">
          <span class="user-email" [title]="user.role">{{ user.email }}</span>
          <button
            class="settings-toggle"
            [routerLink]="['/settings']"
            [queryParams]="{ tab: 'notifications' }"
            [title]="'navbar.settings' | t"
            [attr.aria-label]="'navbar.settings' | t"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82A1.65 1.65 0 0 0 3 13.09H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
          </button>
          <button class="logout-btn" (click)="logout()" [title]="'navbar.logout' | t">
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
    .brand { display: flex; align-items: center; gap: .6rem; }
    .brand-logo { height: 2.2rem; width: auto; }
    .brand-text { display: flex; flex-direction: column; gap: .1rem; }
    .brand-name { font-size: 1.1rem; font-weight: 700; color: var(--text-primary); }
    .brand-tag { font-size: .72rem; color: var(--accent); }
    .nav-links {
      display: flex;
      list-style: none;
      margin: 0;
      padding: 0;
      gap: .25rem;
    }
    .nav-item { position: relative; }
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
    .nav-icon { display: inline-flex; }
    .nav-icon :is(svg) { width: 16px; height: 16px; }
    .nav-links a,
    .nav-parent {
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
    .nav-parent {
      border: none;
      background: transparent;
      cursor: pointer;
      font-family: inherit;
    }
    .nav-links a:hover,
    .nav-parent:hover { background: var(--bg-surface-hover); color: var(--text-primary); }
    .nav-links a.active,
    .nav-parent.active {
      background: var(--accent-bg);
      color: var(--accent);
    }
    .nav-parent.open { color: var(--text-primary); }
    .nav-caret { margin-left: .1rem; transition: transform .15s; opacity: .7; }
    .nav-caret.open { transform: rotate(180deg); }

    /* Submenu dropdown */
    .nav-submenu {
      position: absolute;
      top: calc(100% + .35rem);
      left: 0;
      min-width: 200px;
      list-style: none;
      margin: 0;
      padding: .35rem;
      background: var(--bg-surface);
      border: 1px solid var(--border-light);
      border-radius: .65rem;
      box-shadow: 0 8px 24px var(--shadow);
      z-index: 150;
      display: none;
      flex-direction: column;
      gap: .1rem;
    }
    .nav-submenu.open { display: flex; }
    .nav-submenu a {
      padding: .5rem .7rem;
      border-radius: .45rem;
      font-weight: 500;
      color: var(--text-tertiary);
      white-space: nowrap;
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

    /* Language picker */
    .lang-picker-wrapper { position: relative; }
    .lang-toggle {
      display: inline-flex;
      align-items: center;
      gap: .3rem;
      height: 2rem;
      padding: 0 .5rem;
      border-radius: .5rem;
      border: 1px solid var(--border);
      background: transparent;
      color: var(--text-muted);
      cursor: pointer;
      transition: background .15s, color .15s;
    }
    .lang-toggle:hover { background: var(--bg-surface-hover); color: var(--text-primary); }
    .lang-code { font-size: .72rem; font-weight: 600; text-transform: uppercase; }
    .lang-popover {
      position: absolute;
      top: 2.4rem;
      right: 0;
      background: var(--bg-surface);
      border: 1px solid var(--border-light);
      border-radius: .65rem;
      padding: .35rem;
      z-index: 200;
      min-width: 150px;
      box-shadow: 0 4px 16px var(--shadow);
      display: flex;
      flex-direction: column;
      gap: .1rem;
    }
    .lang-option {
      display: flex;
      align-items: center;
      gap: .5rem;
      padding: .45rem .6rem;
      border-radius: .45rem;
      border: none;
      background: transparent;
      color: var(--text-tertiary);
      font-family: inherit;
      font-size: .85rem;
      text-align: left;
      cursor: pointer;
      transition: background .15s, color .15s;
    }
    .lang-option:hover { background: var(--bg-surface-hover); color: var(--text-primary); }
    .lang-option.active { background: var(--accent-bg); color: var(--accent); font-weight: 600; }

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

    /* Settings (gear) — opens the settings page on the notifications tab */
    .settings-toggle {
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
    .settings-toggle:hover { background: var(--bg-surface-hover); color: var(--text-primary); }

    @media (max-width: 575.98px) {
      .navbar { padding: .6rem 1rem; }
      .brand-tag { display: none; }
      .brand-logo { height: 1.7rem; }
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
      .nav-item { position: static; }
      .nav-links a,
      .nav-parent { padding: .65rem .85rem; font-size: .9rem; gap: .55rem; width: 100%; }
      .nav-parent { justify-content: flex-start; }
      .nav-caret { margin-left: auto; }
      /* Submenu becomes an inline accordion, not a floating dropdown */
      .nav-submenu {
        position: static;
        box-shadow: none;
        border: none;
        background: transparent;
        padding: 0 0 0 1.6rem;
        min-width: 0;
      }
      .nav-submenu a { padding: .55rem .85rem; }
    }
  `]
})
export class NavbarComponent {
  readonly themeService = inject(ThemeService);
  readonly auth = inject(AuthService);
  readonly i18n = inject(I18nService);
  private readonly profile = inject(ProfileService);
  private readonly router = inject(Router);
  private readonly sanitizer = inject(DomSanitizer);
  private readonly features = inject(FeatureService);

  readonly accentPickerOpen = signal(false);
  readonly langPickerOpen = signal(false);
  readonly mobileMenuOpen = signal(false);
  readonly openMenu = signal<string | null>(null);

  private readonly iconCache = new Map<string, SafeHtml>();

  // `label` holds a stable i18n key (used both as menu identity and, via the
  // `t` pipe in the template, as the displayed text — see translations/*.ts).
  readonly menu: NavItem[] = [
    { label: 'nav.chat', route: '/chat', icon: ICONS.chat },
    {
      label: 'nav.group.models',
      icon: ICONS.models,
      children: [
        { label: 'nav.providers', route: '/providers', icon: ICONS.providers, feature: 'providers' },
        { label: 'nav.discovery', route: '/discovery', icon: ICONS.discovery, feature: 'discovery' },
        { label: 'nav.compare', route: '/compare', icon: ICONS.compare, feature: 'compare' },
        { label: 'nav.stats', route: '/stats', icon: ICONS.stats, feature: 'stats' },
      ],
    },
    {
      label: 'nav.group.tools',
      icon: ICONS.tools,
      children: [
        { label: 'nav.tools', route: '/tools', icon: ICONS.tools, feature: 'tools' },
        { label: 'nav.workflows', route: '/workflows', icon: ICONS.workflow, feature: 'workflows' },
        { label: 'nav.graphWorkflows', route: '/graph-workflows', icon: ICONS.workflow, feature: 'graph_workflows' },
        { label: 'nav.graphWorkflowRuns', route: '/graph-workflows/runs', icon: ICONS.workflow, feature: 'graph_workflows' },
        { label: 'nav.graphWorkflowSchedules', route: '/graph-workflows/schedules', icon: ICONS.workflow, feature: 'graph_workflows' },
        { label: 'nav.graphWorkflowRunners', route: '/graph-workflows/runners', icon: ICONS.workflow, feature: 'graph_workflows' },
        { label: 'nav.reminders', route: '/reminders', icon: ICONS.reminders, feature: 'reminders' },
        { label: 'nav.mcp', route: '/mcp', icon: ICONS.mcp, adminOnly: true, feature: 'mcp' },
        { label: 'nav.workspaces', route: '/workspaces', icon: ICONS.workspace, feature: 'workspaces' },
      ],
    },
    {
      label: 'nav.group.resources',
      icon: ICONS.resources,
      children: [
        { label: 'nav.templates', route: '/templates', icon: ICONS.template, feature: 'templates' },
        { label: 'nav.tags', route: '/tags', icon: ICONS.tag, feature: 'tags' },
        { label: 'nav.knowledge', route: '/knowledge', icon: ICONS.knowledge, feature: 'knowledge' },
        { label: 'nav.memory', route: '/memory', icon: ICONS.memory, feature: 'memory' },
      ],
    },
    {
      label: 'nav.group.info',
      icon: ICONS.info,
      children: [
        { label: 'nav.help', route: '/help', icon: ICONS.help, feature: 'help' },
        { label: 'nav.info', route: '/info', icon: ICONS.info, feature: 'info' },
        { label: 'nav.settings', route: '/settings', icon: ICONS.settings, adminOnly: true },
        { label: 'nav.ops', route: '/ops', icon: ICONS.ops, adminOnly: true },
      ],
    },
  ];

  iconHtml(inner: string): SafeHtml {
    let cached = this.iconCache.get(inner);
    if (!cached) {
      cached = this.sanitizer.bypassSecurityTrustHtml(
        `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">${inner}</svg>`
      );
      this.iconCache.set(inner, cached);
    }
    return cached;
  }

  /**
   * A leaf/child is visible unless it is admin-only and the user lacks the role,
   * or it maps to a feature disabled by the admin toggles. A group is visible
   * when at least one of its children is.
   */
  isVisible(item: NavItem): boolean {
    if (item.children) {
      return item.children.some((c) => this.isVisible(c));
    }
    if (item.adminOnly && !this.auth.hasRole('admin')) {
      return false;
    }
    return this.features.enabled(item.feature);
  }

  isGroupActive(item: NavItem): boolean {
    return !!item.children?.some((c) => !!c.route && this.router.url.startsWith(c.route));
  }

  toggleMenu(label: string, event: Event): void {
    event.stopPropagation();
    this.openMenu.update((cur) => (cur === label ? null : label));
  }

  onLeafClick(): void {
    this.openMenu.set(null);
    this.closeMobileMenu();
  }

  @HostListener('document:click')
  onDocumentClick(): void {
    this.openMenu.set(null);
    this.accentPickerOpen.set(false);
    this.langPickerOpen.set(false);
  }

  toggleLangPicker(event: Event): void {
    event.stopPropagation();
    this.langPickerOpen.update((v) => !v);
  }

  setLocale(locale: Locale): void {
    this.i18n.setLocale(locale);
    this.langPickerOpen.set(false);
    // Persist on the active profile (best-effort; localStorage already holds it).
    this.profile.updateLocale(locale)?.subscribe({ error: () => {} });
  }

  toggleMobileMenu(): void {
    this.mobileMenuOpen.update((v) => !v);
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

  toggleAccentPicker(event: Event): void {
    event.stopPropagation();
    this.accentPickerOpen.update((v) => !v);
  }

  setAccent(color: string): void {
    this.themeService.setAccent(color);
  }
}
