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

import {
  CommunityBuildResult,
  GlobalSearchResponse,
  GraphCommunity,
  GraphNodeDetail,
  GraphRagStatus,
  KbChunk,
  KbDocument,
  KbGraph,
  RagSource,
  WikiPage,
} from '../models/chat.models';
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

  /** Ingest a web page / URL into the knowledge base (Phase 17). */
  ingestUrl(url: string, profileId: string): Observable<KbDocument> {
    return this.http.post<KbDocument>(`${this.baseUrl}/urls`, {
      url,
      profile_id: profileId,
    });
  }

  /** Delete a document and all its chunks. */
  deleteDocument(id: string): Observable<void> {
    return this.http.delete<void>(`${this.baseUrl}/documents/${id}`);
  }

  /** Preview the stored chunks of a document. */
  listChunks(id: string): Observable<KbChunk[]> {
    return this.http.get<KbChunk[]>(`${this.baseUrl}/documents/${id}/chunks`);
  }

  /** Re-chunk + re-embed a document from its stored source text. */
  reEmbed(id: string): Observable<KbDocument> {
    return this.http.post<KbDocument>(`${this.baseUrl}/documents/${id}/reembed`, {});
  }

  /**
   * Run a retrieval query (debug / "test retrieval"), optionally scoped to a
   * subset of documents (per-conversation KB scoping).
   */
  search(query: string, profileId: string, topK = 4, documentIds?: string[]): Observable<RagSource[]> {
    return this.http.post<RagSource[]>(`${this.baseUrl}/search`, {
      query,
      top_k: topK,
      profile_id: profileId,
      document_ids: documentIds && documentIds.length ? documentIds : undefined,
    });
  }

  /** wikillm: section tree (headings + summaries) of a document. */
  getWiki(id: string): Observable<WikiPage[]> {
    return this.http.get<WikiPage[]>(`${this.baseUrl}/documents/${id}/wiki`);
  }

  /** wikillm: the profile's knowledge graph, optionally scoped to one document. */
  getGraph(profileId: string, documentId?: string): Observable<KbGraph> {
    const params: Record<string, string> = { profile_id: profileId };
    if (documentId) params['document_id'] = documentId;
    return this.http.get<KbGraph>(`${this.baseUrl}/graph`, { params });
  }

  /** wikillm: a graph node with its neighbours and mentioning documents. */
  getNode(nodeId: string, profileId: string): Observable<GraphNodeDetail> {
    return this.http.get<GraphNodeDetail>(`${this.baseUrl}/graph/nodes/${nodeId}`, {
      params: { profile_id: profileId },
    });
  }

  /** wikillm: rebuild wiki + graph + vectors for pre-wikillm documents. */
  reingest(profileId: string): Observable<{ pending: number; rebuilt: number; failed: number }> {
    return this.http.post<{ pending: number; rebuilt: number; failed: number }>(
      `${this.baseUrl}/reingest`,
      {},
      { params: { profile_id: profileId } },
    );
  }

  // ── Phase 28.d: GraphRAG (communities + global search) ──────────────────────
  /** Whether GraphRAG artefacts exist for the profile (drives the panel). */
  graphStatus(profileId: string): Observable<GraphRagStatus> {
    return this.http.get<GraphRagStatus>(`${this.baseUrl}/graph/status`, {
      params: { profile_id: profileId },
    });
  }

  /** Detected entity communities with their summaries, largest first. */
  listCommunities(profileId: string): Observable<GraphCommunity[]> {
    return this.http.get<GraphCommunity[]>(`${this.baseUrl}/graph/communities`, {
      params: { profile_id: profileId },
    });
  }

  /** Re-detect communities for the profile and (re)generate summaries. */
  rebuildCommunities(profileId: string): Observable<CommunityBuildResult> {
    return this.http.post<CommunityBuildResult>(
      `${this.baseUrl}/graph/communities/rebuild`,
      {},
      { params: { profile_id: profileId } },
    );
  }

  /** GraphRAG global search: map-reduce an answer over community summaries. */
  globalSearch(query: string, profileId: string, topCommunities = 5): Observable<GlobalSearchResponse> {
    return this.http.post<GlobalSearchResponse>(`${this.baseUrl}/graph/global-search`, {
      query,
      profile_id: profileId,
      top_communities: topCommunities,
    });
  }
}
