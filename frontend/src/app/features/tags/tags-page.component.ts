import { Component, OnInit, effect, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { Tag } from '../../core/models/chat.models';
import { TagService } from '../../core/services/tag.service';
import { ProfileService } from '../../core/services/profile.service';
import { NotificationService } from '../../core/services/notification.service';
import { I18nService } from '../../core/i18n/i18n.service';
import { TranslatePipe } from '../../core/i18n/translate.pipe';

/** Tag management (per profile). Promoted from the chat sidebar. */
@Component({
  selector: 'app-tags-page',
  standalone: true,
  imports: [CommonModule, FormsModule, TranslatePipe],
  templateUrl: './tags-page.component.html',
  styleUrls: ['./tags-page.component.css'],
})
export class TagsPageComponent implements OnInit {
  private readonly tagService = inject(TagService);
  readonly profileService = inject(ProfileService);
  private readonly notifications = inject(NotificationService);
  private readonly i18n = inject(I18nService);

  readonly TAG_COLORS = ['#d6b279', '#e07070', '#89d39a', '#8ed0ff', '#c89aff', '#ff9a5c', '#5ac8c8', '#ff7eb3'];

  readonly tags = signal<Tag[]>([]);
  readonly loading = signal(false);

  formVisible = false;
  editId: string | null = null;
  formName = '';
  formColor = '#d6b279';

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
    this.tagService.list(this.profileService.currentId).subscribe({
      next: (list) => { this.tags.set(list); this.loading.set(false); },
      error: () => this.loading.set(false),
    });
  }

  showForm(edit?: Tag): void {
    this.formVisible = true;
    if (edit) {
      this.editId = edit.id;
      this.formName = edit.name;
      this.formColor = edit.color;
    } else {
      this.editId = null;
      this.formName = '';
      this.formColor = '#d6b279';
    }
  }

  cancelForm(): void {
    this.formVisible = false;
    this.editId = null;
    this.formName = '';
    this.formColor = '#d6b279';
  }

  save(): void {
    const name = this.formName.trim();
    if (!name) return;
    if (this.editId) {
      this.tagService.update(this.editId, { name, color: this.formColor }).subscribe({
        next: () => { this.cancelForm(); this.load(); },
        error: () => this.notifications.add('error', this.i18n.translate('common.error'), this.i18n.translate('tags.updateFailed')),
      });
    } else {
      this.tagService.create(name, this.formColor, this.profileService.currentId).subscribe({
        next: () => { this.cancelForm(); this.load(); },
        error: () => this.notifications.add('error', this.i18n.translate('common.error'), this.i18n.translate('tags.createFailed')),
      });
    }
  }

  delete(id: string, event: Event): void {
    event.stopPropagation();
    this.tagService.delete(id).subscribe({
      next: () => this.load(),
      error: () => this.notifications.add('error', this.i18n.translate('common.error'), this.i18n.translate('tags.deleteFailed')),
    });
  }
}
