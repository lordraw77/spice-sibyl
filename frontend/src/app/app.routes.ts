import { Routes } from '@angular/router';

import { authGuard, adminGuard, featureGuard } from './core/guards/auth.guard';

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
    canActivate: [authGuard, featureGuard],
    data: { feature: 'discovery' },
    loadComponent: () =>
      import('./features/discovery/discovery-page.component').then(
        (m) => m.DiscoveryPageComponent
      ),
  },
  {
    path: 'providers',
    canActivate: [authGuard, featureGuard],
    data: { feature: 'providers' },
    loadComponent: () =>
      import('./features/providers/providers-page.component').then(
        (m) => m.ProvidersPageComponent
      ),
  },
  {
    path: 'stats',
    canActivate: [authGuard, featureGuard],
    data: { feature: 'stats' },
    loadComponent: () =>
      import('./features/stats/stats-page.component').then((m) => m.StatsPageComponent),
  },
  {
    path: 'compare',
    canActivate: [authGuard, featureGuard],
    data: { feature: 'compare' },
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
    // Open to every authenticated user: the per-user tabs (notifications,
    // timezone) live here; admin-only tabs are gated inside the component.
    path: 'settings',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/settings/settings-page.component').then((m) => m.SettingsPageComponent),
  },
  {
    path: 'tools',
    canActivate: [authGuard, featureGuard],
    data: { feature: 'tools' },
    loadComponent: () =>
      import('./features/tools/tools-page.component').then((m) => m.ToolsPageComponent),
  },
  {
    path: 'workflows',
    canActivate: [authGuard, featureGuard],
    data: { feature: 'workflows' },
    loadComponent: () =>
      import('./features/workflows/workflows-page.component').then(
        (m) => m.WorkflowsPageComponent
      ),
  },
  {
    path: 'graph-workflows/runs',
    canActivate: [authGuard, featureGuard],
    data: { feature: 'graph_workflows' },
    loadComponent: () =>
      import('./features/workflows/workflow-runs-page.component').then(
        (m) => m.WorkflowRunsPageComponent
      ),
  },
  {
    path: 'graph-workflows/schedules',
    canActivate: [authGuard, featureGuard],
    data: { feature: 'graph_workflows' },
    loadComponent: () =>
      import('./features/workflows/workflow-schedules-page.component').then(
        (m) => m.WorkflowSchedulesPageComponent
      ),
  },
  {
    // Phase 46 (roadmap fase 14.1) — remote runners: register/list/revoke.
    path: 'graph-workflows/runners',
    canActivate: [authGuard, featureGuard],
    data: { feature: 'graph_workflows' },
    loadComponent: () =>
      import('./features/workflows/workflow-runners-page.component').then(
        (m) => m.WorkflowRunnersPageComponent
      ),
  },
  {
    path: 'graph-workflows',
    canActivate: [authGuard, featureGuard],
    data: { feature: 'graph_workflows' },
    loadComponent: () =>
      import('./features/workflows/graph-workflow-page.component').then(
        (m) => m.GraphWorkflowPageComponent
      ),
  },
  {
    // Roadmap fase 1 (1.2): per-workflow shell with Editor | Runs | Schedules
    // tabs scoped to :id. Declared after the literal /runs and /schedules
    // routes so those keep matching the global pages.
    path: 'graph-workflows/:id',
    canActivate: [authGuard, featureGuard],
    data: { feature: 'graph_workflows' },
    loadComponent: () =>
      import('./features/workflows/workflow-shell.component').then((m) => m.WorkflowShellComponent),
    children: [
      {
        path: '',
        loadComponent: () =>
          import('./features/workflows/graph-workflow-page.component').then(
            (m) => m.GraphWorkflowPageComponent
          ),
      },
      {
        path: 'runs',
        loadComponent: () =>
          import('./features/workflows/workflow-runs-page.component').then(
            (m) => m.WorkflowRunsPageComponent
          ),
      },
      {
        path: 'schedules',
        loadComponent: () =>
          import('./features/workflows/workflow-schedules-page.component').then(
            (m) => m.WorkflowSchedulesPageComponent
          ),
      },
      {
        // Phase 39 (roadmap fase 7.4/7.3): per-node health metrics + audit trail.
        path: 'health',
        loadComponent: () =>
          import('./features/workflows/workflow-health-page.component').then(
            (m) => m.WorkflowHealthPageComponent
          ),
      },
    ],
  },
  {
    path: 'reminders',
    canActivate: [authGuard, featureGuard],
    data: { feature: 'reminders' },
    loadComponent: () =>
      import('./features/reminders/reminders-page.component').then(
        (m) => m.RemindersPageComponent
      ),
  },
  {
    path: 'mcp',
    canActivate: [authGuard, adminGuard, featureGuard],
    data: { feature: 'mcp' },
    loadComponent: () =>
      import('./features/mcp/mcp-page.component').then((m) => m.McpPageComponent),
  },
  {
    path: 'workspaces',
    canActivate: [authGuard, featureGuard],
    data: { feature: 'workspaces' },
    loadComponent: () =>
      import('./features/workspaces/workspaces-page.component').then(
        (m) => m.WorkspacesPageComponent
      ),
  },
  {
    path: 'templates',
    canActivate: [authGuard, featureGuard],
    data: { feature: 'templates' },
    loadComponent: () =>
      import('./features/templates/templates-page.component').then(
        (m) => m.TemplatesPageComponent
      ),
  },
  {
    path: 'tags',
    canActivate: [authGuard, featureGuard],
    data: { feature: 'tags' },
    loadComponent: () =>
      import('./features/tags/tags-page.component').then((m) => m.TagsPageComponent),
  },
  {
    path: 'knowledge',
    canActivate: [authGuard, featureGuard],
    data: { feature: 'knowledge' },
    loadComponent: () =>
      import('./features/knowledge/knowledge-page.component').then(
        (m) => m.KnowledgePageComponent
      ),
  },
  {
    path: 'memory',
    canActivate: [authGuard, featureGuard],
    data: { feature: 'memory' },
    loadComponent: () =>
      import('./features/memory/memory-page.component').then((m) => m.MemoryPageComponent),
  },
  {
    path: 'help',
    canActivate: [authGuard, featureGuard],
    data: { feature: 'help' },
    loadComponent: () =>
      import('./features/help/help-page.component').then((m) => m.HelpPageComponent),
  },
  {
    path: 'help/:slug',
    canActivate: [authGuard, featureGuard],
    data: { feature: 'help' },
    loadComponent: () =>
      import('./features/help/help-page.component').then((m) => m.HelpPageComponent),
  },
  {
    path: 'info',
    canActivate: [authGuard, featureGuard],
    data: { feature: 'info' },
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
