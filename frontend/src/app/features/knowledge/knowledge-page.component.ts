import { Component, OnInit, effect, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { catchError, forkJoin, map, of } from 'rxjs';

import { KbDocument } from '../../core/models/chat.models';
import { KnowledgeService } from '../../core/services/knowledge.service';
import { ProfileService } from '../../core/services/profile.service';
import { NotificationService } from '../../core/services/notification.service';

/** Knowledge base (RAG) document management. Promoted from the chat sidebar. */
@Component({
  selector: 'app-knowledge-page',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './knowledge-page.component.html',
  styleUrls: ['./knowledge-page.component.css'],
})
export class KnowledgePageComponent implements OnInit {
  private readonly knowledgeService = inject(KnowledgeService);
  readonly profileService = inject(ProfileService);
  private readonly notifications = inject(NotificationService);

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
        this.notifications.add('error', 'Formato non supportato', `"${file.name}": usa PDF, TXT, DOCX o Markdown.`);
        continue;
      }
      if (file.size > 20 * 1024 * 1024) {
        this.notifications.add('error', 'File troppo grande', `"${file.name}": dimensione massima 20 MB.`);
        continue;
      }
      const dup = this.kbDocuments().some(
        (d) => d.filename === file.name && d.size_bytes === file.size,
      );
      if (dup) {
        this.notifications.add('info', 'Già presente', `"${file.name}" è già nella knowledge base.`);
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
      if (added.length) parts.push(`${added.length} aggiunti`);
      if (duplicates) parts.push(`${duplicates} duplicati ignorati`);
      if (failed) parts.push(`${failed} falliti`);

      if (added.length) {
        this.notifications.add('success', 'Caricamento completato', parts.join(' · '));
      } else if (duplicates && !failed) {
        this.notifications.add('info', 'Nessun nuovo documento', `${duplicates} duplicati ignorati.`);
      }
    });
  }

  ingestUrl(): void {
    const url = this.kbUrl().trim();
    if (!url) return;
    if (!/^https?:\/\//i.test(url)) {
      this.notifications.add('error', 'URL non valido', 'Inserisci un URL http(s) completo.');
      return;
    }
    this.kbUploading.set(true);
    this.knowledgeService.ingestUrl(url, this.profileService.currentId).subscribe({
      next: (doc) => {
        this.kbDocuments.update((docs) => [doc, ...docs.filter((d) => d.id !== doc.id)]);
        this.kbUploading.set(false);
        this.kbUrl.set('');
        this.notifications.add('success', 'Pagina aggiunta', `"${doc.filename}" indicizzata (${doc.chunk_count} chunk).`);
      },
      error: (err: Error) => {
        this.kbUploading.set(false);
        this.notifications.add('error', 'Ingest URL fallito', err?.message || 'Impossibile leggere la pagina.');
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
        this.notifications.add('success', 'Re-embed completato', `"${doc.filename}" re-indicizzato (${doc.chunk_count} chunk).`);
      },
      error: (err: Error) => {
        this.notifications.add('error', 'Re-embed fallito', err?.message || 'Impossibile re-indicizzare.');
      },
    });
  }
}
