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
    path: 'mcp',
    canActivate: [authGuard, adminGuard],
    loadComponent: () =>
      import('./features/mcp/mcp-page.component').then((m) => m.McpPageComponent),
  },
  {
    // Public read-only shared conversation view — no auth required.
    path: 'shared/:token',
    loadComponent: () =>
      import('./features/shared/shared-view.component').then((m) => m.SharedViewComponent),
  },
  { path: '**', redirectTo: 'chat' },
];
