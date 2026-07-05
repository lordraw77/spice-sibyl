import { Component, OnInit, effect, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';

import { PromptTemplate } from '../../core/models/chat.models';
import { TemplateService } from '../../core/services/template.service';
import { ProfileService } from '../../core/services/profile.service';
import { UserPreferencesService } from '../../core/services/user-preferences.service';
import { NotificationService } from '../../core/services/notification.service';
import { I18nService } from '../../core/i18n/i18n.service';
import { TranslatePipe } from '../../core/i18n/translate.pipe';

/** Prompt templates management (per profile). Promoted from the chat sidebar. */
@Component({
  selector: 'app-templates-page',
  standalone: true,
  imports: [CommonModule, FormsModule, TranslatePipe],
  templateUrl: './templates-page.component.html',
  styleUrls: ['./templates-page.component.css'],
})
export class TemplatesPageComponent implements OnInit {
  private readonly templateService = inject(TemplateService);
  readonly profileService = inject(ProfileService);
  private readonly userPrefs = inject(UserPreferencesService);
  private readonly notifications = inject(NotificationService);
  private readonly i18n = inject(I18nService);
  private readonly router = inject(Router);

  readonly templates = signal<PromptTemplate[]>([]);
  readonly loading = signal(false);

  formVisible = false;
  editId: string | null = null;
  formName = '';
  formContent = '';

  constructor() {
    // Reload when the active profile changes.
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
    this.templateService.list(this.profileService.currentId).subscribe({
      next: (list) => { this.templates.set(list); this.loading.set(false); },
      error: () => this.loading.set(false),
    });
  }

  showForm(edit?: PromptTemplate): void {
    this.formVisible = true;
    if (edit) {
      this.editId = edit.id;
      this.formName = edit.name;
      this.formContent = edit.content;
    } else {
      this.editId = null;
      this.formName = '';
      this.formContent = '';
    }
  }

  cancelForm(): void {
    this.formVisible = false;
    this.editId = null;
    this.formName = '';
    this.formContent = '';
  }

  save(): void {
    const name = this.formName.trim();
    const content = this.formContent.trim();
    if (!name || !content) return;
    if (this.editId) {
      this.templateService.update(this.editId, { name, content }).subscribe({
        next: () => { this.cancelForm(); this.load(); },
        error: () => this.notifications.add('error', this.i18n.translate('common.error'), this.i18n.translate('tpl.updateFailed')),
      });
    } else {
      this.templateService.create(name, content, this.profileService.currentId).subscribe({
        next: () => { this.cancelForm(); this.load(); },
        error: () => this.notifications.add('error', this.i18n.translate('common.error'), this.i18n.translate('tpl.createFailed')),
      });
    }
  }

  delete(id: string, event: Event): void {
    event.stopPropagation();
    this.templateService.delete(id).subscribe({
      next: () => this.load(),
      error: () => this.notifications.add('error', this.i18n.translate('common.error'), this.i18n.translate('tpl.deleteFailed')),
    });
  }

  /** Set this template as the persistent system prompt used by the chat. */
  apply(t: PromptTemplate): void {
    this.userPrefs.set('systemPrompt', t.content);
    this.notifications.add('success', this.i18n.translate('tpl.applied'), this.i18n.translate('tpl.appliedBody', { name: t.name }));
    this.router.navigate(['/chat']);
  }
}
