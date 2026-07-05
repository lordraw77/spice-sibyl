import { Component, OnInit, effect, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { catchError, forkJoin, map, of } from 'rxjs';

import { KbDocument } from '../../core/models/chat.models';
import { KnowledgeService } from '../../core/services/knowledge.service';
import { ProfileService } from '../../core/services/profile.service';
import { NotificationService } from '../../core/services/notification.service';
import { I18nService } from '../../core/i18n/i18n.service';
import { TranslatePipe } from '../../core/i18n/translate.pipe';

/** Knowledge base (RAG) document management. Promoted from the chat sidebar. */
@Component({
  selector: 'app-knowledge-page',
  standalone: true,
  imports: [CommonModule, FormsModule, TranslatePipe],
  templateUrl: './knowledge-page.component.html',
  styleUrls: ['./knowledge-page.component.css'],
})
export class KnowledgePageComponent implements OnInit {
  private readonly knowledgeService = inject(KnowledgeService);
  readonly profileService = inject(ProfileService);
  private readonly notifications = inject(NotificationService);
  private readonly i18n = inject(I18nService);

  readonly kbDocuments = signal<KbDocument[]>([]);
  readonly kbUploading = signal(false);
  readonly kbUrl = signal('');
  readonly loading = signal(false);

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
    this.knowledgeService.listDocuments(this.profileService.currentId).subscribe({
      next: (docs) => { this.kbDocuments.set(docs); this.loading.set(false); },
      error: () => this.loading.set(false),
    });
  }

  onFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const files = Array.from(input.files ?? []);
    input.value = '';
    if (!files.length) return;

    const allowed = /\.(pdf|txt|md|markdown|docx)$/i;
    const valid: File[] = [];
    for (const file of files) {
      if (!allowed.test(file.name)) {
        this.notifications.add('error', this.i18n.translate('kb.badFormatTitle'), this.i18n.translate('kb.badFormatBody', { name: file.name }));
        continue;
      }
      if (file.size > 20 * 1024 * 1024) {
        this.notifications.add('error', this.i18n.translate('chat.err.fileTooBigTitle'), this.i18n.translate('kb.tooBigBody', { name: file.name }));
        continue;
      }
      const dup = this.kbDocuments().some(
        (d) => d.filename === file.name && d.size_bytes === file.size,
      );
      if (dup) {
        this.notifications.add('info', this.i18n.translate('kb.dupTitle'), this.i18n.translate('kb.dupBody', { name: file.name }));
        continue;
      }
      valid.push(file);
    }
    if (!valid.length) return;

    this.kbUploading.set(true);
    const uploads = valid.map((file) =>
      this.knowledgeService.uploadDocument(file, this.profileService.currentId).pipe(
        map((doc) => ({ ok: true, doc } as const)),
        catchError((err: { status?: number }) => of({ ok: false, status: err?.status } as const)),
      ),
    );
    forkJoin(uploads).subscribe((results) => {
      this.kbUploading.set(false);
      const added = results.filter((r) => r.ok).map((r) => (r as { ok: true; doc: KbDocument }).doc);
      if (added.length) {
        this.kbDocuments.update((docs) => [
          ...added,
          ...docs.filter((d) => !added.some((a) => a.id === d.id)),
        ]);
      }
      const duplicates = results.filter((r) => !r.ok && (r as { status?: number }).status === 409).length;
      const failed = results.filter((r) => !r.ok && (r as { status?: number }).status !== 409).length;

      const parts: string[] = [];
      if (added.length) parts.push(this.i18n.translate('kb.parts.added', { n: added.length }));
      if (duplicates) parts.push(this.i18n.translate('kb.parts.dups', { n: duplicates }));
      if (failed) parts.push(this.i18n.translate('kb.parts.failed', { n: failed }));

      if (added.length) {
        this.notifications.add('success', this.i18n.translate('kb.uploadDone'), parts.join(' · '));
      } else if (duplicates && !failed) {
        this.notifications.add('info', this.i18n.translate('kb.noNew'), this.i18n.translate('kb.dupIgnored', { n: duplicates }));
      }
    });
  }

  ingestUrl(): void {
    const url = this.kbUrl().trim();
    if (!url) return;
    if (!/^https?:\/\//i.test(url)) {
      this.notifications.add('error', this.i18n.translate('kb.badUrlTitle'), this.i18n.translate('kb.badUrlBody'));
      return;
    }
    this.kbUploading.set(true);
    this.knowledgeService.ingestUrl(url, this.profileService.currentId).subscribe({
      next: (doc) => {
        this.kbDocuments.update((docs) => [doc, ...docs.filter((d) => d.id !== doc.id)]);
        this.kbUploading.set(false);
        this.kbUrl.set('');
        this.notifications.add('success', this.i18n.translate('kb.pageAdded'), this.i18n.translate('kb.pageAddedBody', { name: doc.filename, n: doc.chunk_count }));
      },
      error: (err: Error) => {
        this.kbUploading.set(false);
        this.notifications.add('error', this.i18n.translate('kb.ingestFailed'), err?.message || this.i18n.translate('kb.ingestFailedBody'));
      },
    });
  }

  deleteDoc(id: string, event: Event): void {
    event.stopPropagation();
    this.knowledgeService.deleteDocument(id).subscribe({
      next: () => this.kbDocuments.update((docs) => docs.filter((d) => d.id !== id)),
      error: () => {},
    });
  }

  reEmbed(id: string, event: Event): void {
    event.stopPropagation();
    this.knowledgeService.reEmbed(id).subscribe({
      next: (doc) => {
        this.kbDocuments.update((docs) => docs.map((d) => (d.id === doc.id ? doc : d)));
        this.notifications.add('success', this.i18n.translate('kb.reembedDone'), this.i18n.translate('kb.reembedDoneBody', { name: doc.filename, n: doc.chunk_count }));
      },
      error: (err: Error) => {
        this.notifications.add('error', this.i18n.translate('kb.reembedFailed'), err?.message || this.i18n.translate('kb.reembedFailedBody'));
      },
    });
  }
}
