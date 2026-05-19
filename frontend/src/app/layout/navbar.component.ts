import { Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router, RouterLink, RouterLinkActive } from '@angular/router';

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
      <ul class="nav-links">
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
      </ul>
    </nav>
  `,
  styles: [`
    .navbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: .75rem 2rem;
      background: rgba(17, 20, 27, 0.97);
      border-bottom: 1px solid rgba(255,255,255,.08);
      position: sticky;
      top: 0;
      z-index: 100;
    }
    .brand { display: flex; flex-direction: column; gap: .1rem; }
    .brand-name { font-size: 1.1rem; font-weight: 700; color: #f7f3ea; }
    .brand-tag { font-size: .72rem; color: #d6b279; }
    .nav-links {
      display: flex;
      list-style: none;
      margin: 0;
      padding: 0;
      gap: .25rem;
    }
    .nav-links a {
      display: flex;
      align-items: center;
      gap: .45rem;
      padding: .45rem 1rem;
      border-radius: .55rem;
      text-decoration: none;
      color: #9fa8ba;
      font-size: .9rem;
      font-weight: 500;
      transition: background .15s, color .15s;
    }
    .nav-links a:hover { background: rgba(255,255,255,.06); color: #f7f3ea; }
    .nav-links a.active {
      background: rgba(214,178,121,.12);
      color: #d6b279;
    }
    @media (max-width: 575.98px) {
      .navbar { padding: .6rem 1rem; }
      .brand-tag { display: none; }
      .nav-links a { padding: .45rem .65rem; font-size: .85rem; gap: .35rem; }
      .nav-links a svg { width: 15px; height: 15px; }
    }
  `]
})
export class NavbarComponent {}
