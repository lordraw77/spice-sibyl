import { Routes } from '@angular/router';

export const routes: Routes = [
  { path: '', redirectTo: 'chat', pathMatch: 'full' },
  {
    path: 'chat',
    loadComponent: () =>
      import('./features/chat/chat-page.component').then((m) => m.ChatPageComponent),
  },
  {
    path: 'discovery',
    loadComponent: () =>
      import('./features/discovery/discovery-page.component').then(
        (m) => m.DiscoveryPageComponent
      ),
  },
  { path: '**', redirectTo: 'chat' },
];
