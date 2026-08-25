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
  templateUrl: './navbar.component.html',
  styleUrls: ['./navbar.component.css'],
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
