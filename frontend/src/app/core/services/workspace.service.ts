/**
 * WorkspaceService — HTTP client for Phase 20.a shared workspaces.
 *
 * Wraps /v1/workspaces: workspace CRUD, membership management, and sharing of
 * conversations / knowledge base documents into a workspace. Access control is
 * enforced server-side by the caller's role (owner > admin > editor > viewer).
 */
import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

import { AppConfigService } from '../config/app-config.service';

export type WorkspaceRole = 'owner' | 'admin' | 'editor' | 'viewer';

export interface Workspace {
  id: string;
  name: string;
  owner_id: string;
  role: WorkspaceRole;
  member_count: number;
  created_at: number;
  updated_at: number;
}

export interface WorkspaceMember {
  user_id: string;
  email: string;
  role: WorkspaceRole;
  added_at: number;
}

export interface SharedConversation {
  conversation_id: string;
  title: string;
  model: string;
  shared_by: string;
  shared_at: number;
  updated_at: number;
}

export interface SharedDocument {
  document_id: string;
  filename: string;
  chunk_count: number;
  status: string;
  shared_by: string;
  shared_at: number;
}

// Descending privilege — used by the UI to gate actions client-side.
const ROLE_RANK: Record<WorkspaceRole, number> = {
  viewer: 0, editor: 1, admin: 2, owner: 3,
};

export function roleAtLeast(role: WorkspaceRole, minimum: WorkspaceRole): boolean {
  return ROLE_RANK[role] >= ROLE_RANK[minimum];
}

@Injectable({ providedIn: 'root' })
export class WorkspaceService {
  private readonly http = inject(HttpClient);
  private readonly config = inject(AppConfigService);

  private get baseUrl(): string {
    return `${this.config.apiUrl}/workspaces`;
  }

  list(): Observable<Workspace[]> {
    return this.http.get<Workspace[]>(this.baseUrl);
  }

  create(name: string): Observable<Workspace> {
    return this.http.post<Workspace>(this.baseUrl, { name });
  }

  rename(id: string, name: string): Observable<Workspace> {
    return this.http.patch<Workspace>(`${this.baseUrl}/${id}`, { name });
  }

  delete(id: string): Observable<void> {
    return this.http.delete<void>(`${this.baseUrl}/${id}`);
  }

  // --- members ---
  members(id: string): Observable<WorkspaceMember[]> {
    return this.http.get<WorkspaceMember[]>(`${this.baseUrl}/${id}/members`);
  }

  addMember(id: string, email: string, role: WorkspaceRole): Observable<WorkspaceMember[]> {
    return this.http.post<WorkspaceMember[]>(`${this.baseUrl}/${id}/members`, { email, role });
  }

  updateMemberRole(id: string, userId: string, role: WorkspaceRole): Observable<WorkspaceMember[]> {
    return this.http.patch<WorkspaceMember[]>(`${this.baseUrl}/${id}/members/${userId}`, { role });
  }

  removeMember(id: string, userId: string): Observable<void> {
    return this.http.delete<void>(`${this.baseUrl}/${id}/members/${userId}`);
  }

  // --- shared conversations ---
  conversations(id: string): Observable<SharedConversation[]> {
    return this.http.get<SharedConversation[]>(`${this.baseUrl}/${id}/conversations`);
  }

  shareConversation(id: string, conversationId: string): Observable<SharedConversation[]> {
    return this.http.post<SharedConversation[]>(`${this.baseUrl}/${id}/conversations`, {
      conversation_id: conversationId,
    });
  }

  unshareConversation(id: string, conversationId: string): Observable<void> {
    return this.http.delete<void>(`${this.baseUrl}/${id}/conversations/${conversationId}`);
  }

  // --- shared documents ---
  documents(id: string): Observable<SharedDocument[]> {
    return this.http.get<SharedDocument[]>(`${this.baseUrl}/${id}/documents`);
  }

  shareDocument(id: string, documentId: string): Observable<SharedDocument[]> {
    return this.http.post<SharedDocument[]>(`${this.baseUrl}/${id}/documents`, {
      document_id: documentId,
    });
  }

  unshareDocument(id: string, documentId: string): Observable<void> {
    return this.http.delete<void>(`${this.baseUrl}/${id}/documents/${documentId}`);
  }
}
