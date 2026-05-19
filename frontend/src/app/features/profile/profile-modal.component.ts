import { Component, OnInit, inject, signal, output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { ProfileService } from '../../core/services/profile.service';
import { Profile } from '../../core/models/chat.models';

@Component({
  selector: 'app-profile-modal',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="modal-backdrop">
      <div class="modal-box">
        <div class="modal-header">
          <div class="modal-logo">S</div>
          <h2>Chi sei?</h2>
          <p class="modal-sub">Seleziona il tuo profilo per accedere alla tua cronologia chat.</p>
        </div>

        <!-- Existing profiles -->
        <ul class="profile-list" *ngIf="profiles().length">
          <li
            *ngFor="let p of profiles()"
            class="profile-item"
            (click)="pick(p)"
          >
            <span class="profile-avatar">{{ p.name.charAt(0).toUpperCase() }}</span>
            <span class="profile-name">{{ p.name }}</span>
            <button class="profile-del" (click)="remove(p, $event)" title="Elimina">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
              </svg>
            </button>
          </li>
        </ul>

        <!-- New profile form -->
        <div class="new-profile-form" *ngIf="creating()">
          <input
            #nameInput
            class="profile-input"
            type="text"
            [(ngModel)]="newName"
            placeholder="Il tuo nome…"
            maxlength="40"
            (keydown.enter)="create()"
            (keydown.escape)="creating.set(false)"
            autofocus
          />
          <div class="form-actions">
            <button class="btn-cancel" (click)="creating.set(false)">Annulla</button>
            <button class="btn-create" [disabled]="!newName.trim()" (click)="create()">Crea</button>
          </div>
        </div>

        <button class="btn-new-profile" *ngIf="!creating()" (click)="creating.set(true)">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
            <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
          </svg>
          Nuovo profilo
        </button>
      </div>
    </div>
  `,
  styles: [`
    .modal-backdrop {
      position: fixed;
      inset: 0;
      z-index: 1000;
      background: rgba(6, 8, 12, 0.88);
      backdrop-filter: blur(6px);
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 1rem;
    }

    .modal-box {
      background: #13161f;
      border: 1px solid rgba(255,255,255,.1);
      border-radius: 1.25rem;
      padding: 2rem;
      width: 100%;
      max-width: 360px;
      box-shadow: 0 24px 80px rgba(0,0,0,.6);
    }

    .modal-header { text-align: center; margin-bottom: 1.75rem; }

    .modal-logo {
      width: 2.8rem;
      height: 2.8rem;
      border-radius: .75rem;
      background: rgba(214,178,121,.15);
      border: 1px solid rgba(214,178,121,.25);
      color: #d6b279;
      font-size: 1.2rem;
      font-weight: 700;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      margin-bottom: 1rem;
    }

    h2 {
      margin: 0 0 .4rem;
      font-size: 1.3rem;
      font-weight: 700;
      color: #f7f3ea;
    }

    .modal-sub {
      margin: 0;
      font-size: .84rem;
      color: #6b7485;
      line-height: 1.5;
    }

    .profile-list {
      list-style: none;
      padding: 0;
      margin: 0 0 1rem;
      display: flex;
      flex-direction: column;
      gap: .4rem;
    }

    .profile-item {
      display: flex;
      align-items: center;
      gap: .75rem;
      padding: .65rem .85rem;
      border-radius: .75rem;
      border: 1px solid rgba(255,255,255,.07);
      cursor: pointer;
      transition: background .15s, border-color .15s;
    }

    .profile-item:hover {
      background: rgba(255,255,255,.04);
      border-color: rgba(214,178,121,.2);
    }

    .profile-avatar {
      width: 2rem;
      height: 2rem;
      border-radius: .5rem;
      background: rgba(214,178,121,.12);
      border: 1px solid rgba(214,178,121,.18);
      color: #d6b279;
      font-size: .85rem;
      font-weight: 700;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
    }

    .profile-name {
      flex: 1;
      font-size: .92rem;
      color: #e6e9ef;
    }

    .profile-del {
      background: transparent;
      border: none;
      color: transparent;
      display: inline-flex;
      align-items: center;
      cursor: pointer;
      padding: .2rem;
      border-radius: .3rem;
      transition: color .15s, background .15s;
      line-height: 0;
    }

    .profile-item:hover .profile-del { color: #5a6070; }
    .profile-del:hover { color: #e07070 !important; background: rgba(224,112,112,.1); }

    .btn-new-profile {
      width: 100%;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: .45rem;
      padding: .65rem;
      border-radius: .75rem;
      border: 1px dashed rgba(255,255,255,.12);
      background: transparent;
      color: #6b7485;
      font-size: .88rem;
      cursor: pointer;
      transition: border-color .15s, color .15s, background .15s;
    }

    .btn-new-profile:hover {
      border-color: rgba(214,178,121,.25);
      color: #d6b279;
      background: rgba(214,178,121,.05);
    }

    .new-profile-form { display: flex; flex-direction: column; gap: .75rem; margin-bottom: 1rem; }

    .profile-input {
      background: rgba(10,12,18,.8);
      border: 1px solid rgba(255,255,255,.12);
      border-radius: .65rem;
      padding: .65rem .85rem;
      color: #f7f3ea;
      font-size: .93rem;
      font-family: inherit;
      outline: none;
      transition: border-color .18s;
    }

    .profile-input:focus {
      border-color: rgba(214,178,121,.45);
      box-shadow: 0 0 0 3px rgba(193,165,107,.08);
    }

    .form-actions { display: flex; gap: .5rem; justify-content: flex-end; }

    .btn-cancel {
      padding: .45rem .9rem;
      border-radius: .55rem;
      border: 1px solid rgba(255,255,255,.08);
      background: transparent;
      color: #9fa8ba;
      font-size: .85rem;
      cursor: pointer;
      transition: background .15s;
    }

    .btn-cancel:hover { background: rgba(255,255,255,.05); }

    .btn-create {
      padding: .45rem .9rem;
      border-radius: .55rem;
      border: none;
      background: #c1a56b;
      color: #0b0d11;
      font-size: .85rem;
      font-weight: 600;
      cursor: pointer;
      transition: background .15s, opacity .15s;
    }

    .btn-create:hover:not(:disabled) { background: #d6b97a; }
    .btn-create:disabled { opacity: .35; cursor: not-allowed; }
  `],
})
export class ProfileModalComponent implements OnInit {
  private readonly profileService = inject(ProfileService);

  readonly profiles = signal<Profile[]>([]);
  readonly creating = signal(false);
  newName = '';

  ngOnInit(): void {
    this.profileService.list().subscribe({
      next: (list) => this.profiles.set(list),
      error: () => {},
    });
  }

  pick(profile: Profile): void {
    this.profileService.select(profile);
  }

  create(): void {
    const name = this.newName.trim();
    if (!name) return;
    this.profileService.create(name).subscribe({
      next: (p) => {
        this.profiles.update(list => [...list, p]);
        this.newName = '';
        this.creating.set(false);
      },
    });
  }

  remove(profile: Profile, event: Event): void {
    event.stopPropagation();
    this.profileService.delete(profile.id).subscribe({
      next: () => this.profiles.update(list => list.filter(p => p.id !== profile.id)),
    });
  }
}
