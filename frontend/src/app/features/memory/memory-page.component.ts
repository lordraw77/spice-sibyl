import { Component, OnInit, effect, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { ProfileMemory } from '../../core/models/chat.models';
import { MemoryService } from '../../core/services/memory.service';
import { ProfileService } from '../../core/services/profile.service';
import { NotificationService } from '../../core/services/notification.service';

/** Persistent memory management (Phase 19). Promoted from the chat sidebar. */
@Component({
  selector: 'app-memory-page',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './memory-page.component.html',
  styleUrls: ['./memory-page.component.css'],
})
export class MemoryPageComponent implements OnInit {
  private readonly memoryService = inject(MemoryService);
  readonly profileService = inject(ProfileService);
  private readonly notifications = inject(NotificationService);

  readonly categoryIcons: Record<string, string> = {
    preference: '⭐', fact: '💡', project: '📁', instruction: '📌',
  };
  readonly categories: ProfileMemory['category'][] = ['fact', 'preference', 'project', 'instruction'];

  readonly memories = signal<ProfileMemory[]>([]);
  readonly memoryProfileEnabled = signal(true);
  readonly loading = signal(false);

  formContent = '';
  formCategory: ProfileMemory['category'] = 'fact';

  constructor() {
    let first = true;
    effect(() => {
      this.profileService.current();
      if (first) { first = false; return; }
      this.load();
    });
  }

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.memoryService.list().subscribe({
      next: (items) => { this.memories.set(items); this.loading.set(false); },
      error: () => this.loading.set(false),
    });
    this.memoryService.getSettings().subscribe({
      next: (s) => this.memoryProfileEnabled.set(s.memory_enabled),
      error: () => {},
    });
  }

  toggleProfileMemory(): void {
    const next = !this.memoryProfileEnabled();
    this.memoryService.setSettings(next).subscribe({
      next: (s) => this.memoryProfileEnabled.set(s.memory_enabled),
      error: () => this.notifications.add('error', 'Memoria', 'Aggiornamento impostazione fallito.'),
    });
  }

  add(): void {
    const content = this.formContent.trim();
    if (!content) return;
    this.memoryService.create(content, this.formCategory).subscribe({
      next: (mem) => {
        this.memories.update((items) => [mem, ...items]);
        this.formContent = '';
      },
      error: () => this.notifications.add('error', 'Memoria', 'Salvataggio fallito.'),
    });
  }

  toggleItem(mem: ProfileMemory): void {
    this.memoryService.update(mem.id, { enabled: !mem.enabled }).subscribe({
      next: (updated) => this.memories.update((items) => items.map((m) => (m.id === mem.id ? updated : m))),
      error: () => {},
    });
  }

  delete(id: string, event?: Event): void {
    event?.stopPropagation();
    this.memoryService.delete(id).subscribe({
      next: () => this.memories.update((items) => items.filter((m) => m.id !== id)),
      error: () => {},
    });
  }

  forgetAll(): void {
    if (!confirm('Dimenticare tutti i ricordi di questo profilo?')) return;
    this.memoryService.forgetAll().subscribe({
      next: () => {
        this.memories.set([]);
        this.notifications.add('success', 'Memoria', 'Tutti i ricordi sono stati eliminati.');
      },
      error: () => this.notifications.add('error', 'Memoria', 'Eliminazione fallita.'),
    });
  }
}
