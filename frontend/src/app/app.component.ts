import { Component } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { NavbarComponent } from './layout/navbar.component';
import { ToastContainerComponent } from './shared/toast-container/toast-container.component';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterOutlet, NavbarComponent, ToastContainerComponent],
  template: `
    <app-navbar />
    <main class="app-main">
      <router-outlet />
    </main>
    <app-toast-container />
  `,
  styles: [`
    :host { display: block; min-height: 100vh; background: #0b0d11; }
    .app-main { min-height: calc(100vh - 57px); }
  `]
})
export class AppComponent {}
