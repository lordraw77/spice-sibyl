/**
 * KnowledgeService — HTTP client for the RAG knowledge base.
 *
 * Wraps the /v1/knowledge endpoints: list / upload / delete documents and a
 * retrieval-test search.  The active profile is conveyed via the X-Profile-ID
 * header by the app's HTTP interceptor; the profile_id query param is sent as a
 * fallback, mirroring TemplateService / TagService.
 */
import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

import { KbDocument, RagSource } from '../models/chat.models';
import { AppConfigService } from '../config/app-config.service';

@Injectable({ providedIn: 'root' })
export class KnowledgeService {
  private readonly http = inject(HttpClient);
  private readonly config = inject(AppConfigService);

  private get baseUrl(): string {
    return `${this.config.apiUrl}/knowledge`;
  }

  /** List the documents in the profile's knowledge base. */
  listDocuments(profileId: string): Observable<KbDocument[]> {
    return this.http.get<KbDocument[]>(`${this.baseUrl}/documents`, {
      params: { profile_id: profileId },
    });
  }

  /** Upload and ingest a document (PDF, TXT, DOCX, Markdown). */
  uploadDocument(file: File, profileId: string): Observable<KbDocument> {
    const form = new FormData();
    form.append('file', file);
    form.append('profile_id', profileId);
    return this.http.post<KbDocument>(`${this.baseUrl}/documents`, form);
  }

  /** Delete a document and all its chunks. */
  deleteDocument(id: string): Observable<void> {
    return this.http.delete<void>(`${this.baseUrl}/documents/${id}`);
  }

  /** Run a retrieval query (debug / "test retrieval"). */
  search(query: string, profileId: string, topK = 4): Observable<RagSource[]> {
    return this.http.post<RagSource[]>(`${this.baseUrl}/search`, {
      query,
      top_k: topK,
      profile_id: profileId,
    });
  }
}
