import { Component, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute } from '@angular/router';

import { AuthService } from '../../core/services/auth.service';

@Component({
  selector: 'app-login',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="login-backdrop">
      <form class="login-box" (ngSubmit)="submit()">
        <div class="login-header">
          <div class="login-logo">S</div>
          <h2>SpiceSibyl</h2>
          <p class="login-sub">Accedi per continuare.</p>
        </div>

        <label class="field">
          <span>Email</span>
          <input
            class="login-input"
            type="email"
            name="email"
            [(ngModel)]="email"
            autocomplete="username"
            placeholder="nome@esempio.com"
            required
          />
        </label>

        <label class="field">
          <span>Password</span>
          <input
            class="login-input"
            type="password"
            name="password"
            [(ngModel)]="password"
            autocomplete="current-password"
            placeholder="••••••••"
            required
          />
        </label>

        <p class="login-error" *ngIf="error()">{{ error() }}</p>

        <button class="btn-login" type="submit" [disabled]="loading() || !email.trim() || !password">
          {{ loading() ? 'Accesso…' : 'Accedi' }}
        </button>
      </form>
    </div>
  `,
  styles: [`
    .login-backdrop {
      position: fixed;
      inset: 0;
      background: rgba(6, 8, 12, 0.96);
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 1rem;
    }
    .login-box {
      background: #13161f;
      border: 1px solid rgba(255,255,255,.1);
      border-radius: 1.25rem;
      padding: 2rem;
      width: 100%;
      max-width: 360px;
      box-shadow: 0 24px 80px rgba(0,0,0,.6);
      display: flex;
      flex-direction: column;
      gap: 1rem;
    }
    .login-header { text-align: center; margin-bottom: .5rem; }
    .login-logo {
      width: 2.8rem; height: 2.8rem; border-radius: .75rem;
      background: rgba(214,178,121,.15); border: 1px solid rgba(214,178,121,.25);
      color: #d6b279; font-size: 1.2rem; font-weight: 700;
      display: inline-flex; align-items: center; justify-content: center; margin-bottom: 1rem;
    }
    h2 { margin: 0 0 .4rem; font-size: 1.3rem; font-weight: 700; color: #f7f3ea; }
    .login-sub { margin: 0; font-size: .84rem; color: #6b7485; }
    .field { display: flex; flex-direction: column; gap: .35rem; }
    .field span { font-size: .8rem; color: #9fa8ba; }
    .login-input {
      background: rgba(10,12,18,.8); border: 1px solid rgba(255,255,255,.12);
      border-radius: .65rem; padding: .65rem .85rem; color: #f7f3ea;
      font-size: .93rem; font-family: inherit; outline: none; transition: border-color .18s;
    }
    .login-input:focus {
      border-color: rgba(214,178,121,.45); box-shadow: 0 0 0 3px rgba(193,165,107,.08);
    }
    .login-error { margin: 0; font-size: .82rem; color: #e07070; }
    .btn-login {
      margin-top: .5rem; padding: .7rem; border-radius: .65rem; border: none;
      background: #c1a56b; color: #0b0d11; font-size: .92rem; font-weight: 600;
      cursor: pointer; transition: background .15s, opacity .15s;
    }
    .btn-login:hover:not(:disabled) { background: #d6b97a; }
    .btn-login:disabled { opacity: .4; cursor: not-allowed; }
  `],
})
export class LoginComponent {
  private readonly auth = inject(AuthService);
  private readonly route = inject(ActivatedRoute);

  email = '';
  password = '';
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);

  submit(): void {
    if (this.loading() || !this.email.trim() || !this.password) return;
    this.loading.set(true);
    this.error.set(null);
    this.auth.login(this.email.trim(), this.password).subscribe({
      next: () => {
        const redirect = this.route.snapshot.queryParamMap.get('redirect') || '/chat';
        // Full reload so all singletons (ProfileService et al.) reinitialise cleanly
        // for the newly authenticated user.
        window.location.assign(redirect);
      },
      error: (err) => {
        this.loading.set(false);
        this.error.set(
          err?.error?.detail || (err?.status === 401 ? 'Email o password non validi.' : 'Accesso fallito.')
        );
      },
    });
  }
}
