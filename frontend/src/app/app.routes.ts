import { Routes } from '@angular/router';

import { authGuard, adminGuard } from './core/guards/auth.guard';

export const routes: Routes = [
  { path: '', redirectTo: 'chat', pathMatch: 'full' },
  {
    path: 'login',
    loadComponent: () =>
      import('./features/auth/login.component').then((m) => m.LoginComponent),
  },
  {
    path: 'chat',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/chat/chat-page.component').then((m) => m.ChatPageComponent),
  },
  {
    path: 'discovery',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/discovery/discovery-page.component').then(
        (m) => m.DiscoveryPageComponent
      ),
  },
  {
    path: 'providers',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/providers/providers-page.component').then(
        (m) => m.ProvidersPageComponent
      ),
  },
  {
    path: 'stats',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/stats/stats-page.component').then((m) => m.StatsPageComponent),
  },
  {
    path: 'compare',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/compare/compare-page.component').then((m) => m.ComparePageComponent),
  },
  {
    path: 'ops',
    canActivate: [authGuard, adminGuard],
    loadComponent: () =>
      import('./features/ops/ops-page.component').then((m) => m.OpsPageComponent),
  },
  {
    path: 'tools',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/tools/tools-page.component').then((m) => m.ToolsPageComponent),
  },
  {
    path: 'workflows',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/workflows/workflows-page.component').then(
        (m) => m.WorkflowsPageComponent
      ),
  },
  {
    path: 'reminders',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/reminders/reminders-page.component').then(
        (m) => m.RemindersPageComponent
      ),
  },
  {
    path: 'mcp',
    canActivate: [authGuard, adminGuard],
    loadComponent: () =>
      import('./features/mcp/mcp-page.component').then((m) => m.McpPageComponent),
  },
  {
    path: 'workspaces',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/workspaces/workspaces-page.component').then(
        (m) => m.WorkspacesPageComponent
      ),
  },
  {
    path: 'templates',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/templates/templates-page.component').then(
        (m) => m.TemplatesPageComponent
      ),
  },
  {
    path: 'tags',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/tags/tags-page.component').then((m) => m.TagsPageComponent),
  },
  {
    path: 'knowledge',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/knowledge/knowledge-page.component').then(
        (m) => m.KnowledgePageComponent
      ),
  },
  {
    path: 'memory',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/memory/memory-page.component').then((m) => m.MemoryPageComponent),
  },
  {
    path: 'help',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/help/help-page.component').then((m) => m.HelpPageComponent),
  },
  {
    path: 'help/:slug',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/help/help-page.component').then((m) => m.HelpPageComponent),
  },
  {
    path: 'info',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/info/info-page.component').then((m) => m.InfoPageComponent),
  },
  {
    // Public read-only shared conversation view — no auth required.
    path: 'shared/:token',
    loadComponent: () =>
      import('./features/shared/shared-view.component').then((m) => m.SharedViewComponent),
  },
  { path: '**', redirectTo: 'chat' },
];
