import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import {
  SharedConversation,
  SharedDocument,
  Workspace,
  WorkspaceMember,
  WorkspaceRole,
  WorkspaceService,
  roleAtLeast,
} from '../../core/services/workspace.service';
import { ConversationService } from '../../core/services/conversation.service';
import { KnowledgeService } from '../../core/services/knowledge.service';
import { ProfileService } from '../../core/services/profile.service';
import { NotificationService } from '../../core/services/notification.service';
import { I18nService } from '../../core/i18n/i18n.service';
import { TranslatePipe } from '../../core/i18n/translate.pipe';
import { ConversationSummary, KbDocument } from '../../core/models/chat.models';
import { CommentsThreadComponent } from './comments-thread.component';

/**
 * Phase 20.a — shared workspaces management page.
 *
 * Left: the workspaces the user belongs to (+ create). Right: the selected
 * workspace's members, shared conversations and shared knowledge base
 * documents, with role-gated actions.
 */
@Component({
  selector: 'app-workspaces-page',
  standalone: true,
  imports: [CommonModule, FormsModule, CommentsThreadComponent, TranslatePipe],
  templateUrl: './workspaces-page.component.html',
  styleUrls: ['./workspaces-page.component.css'],
})
export class WorkspacesPageComponent implements OnInit {
  private readonly api = inject(WorkspaceService);
  private readonly conversationsApi = inject(ConversationService);
  private readonly knowledgeApi = inject(KnowledgeService);
  private readonly profiles = inject(ProfileService);
  private readonly notify = inject(NotificationService);
  private readonly i18n = inject(I18nService);

  readonly workspaces = signal<Workspace[]>([]);
  readonly selected = signal<Workspace | null>(null);
  readonly members = signal<WorkspaceMember[]>([]);
  readonly sharedConversations = signal<SharedConversation[]>([]);
  readonly sharedDocuments = signal<SharedDocument[]>([]);
  readonly loading = signal(false);

  // Phase 20.b: the shared conversation whose comment thread is open (if any).
  readonly commentsFor = signal<SharedConversation | null>(null);

  // My own resources (for the share pickers).
  readonly myConversations = signal<ConversationSummary[]>([]);
  readonly myDocuments = signal<KbDocument[]>([]);

  // Form state
  newWorkspaceName = '';
  inviteEmail = '';
  inviteRole: WorkspaceRole = 'viewer';
  shareConversationId = '';
  shareDocumentId = '';

  readonly assignableRoles: WorkspaceRole[] = ['viewer', 'editor', 'admin'];

  readonly myRole = computed<WorkspaceRole | null>(() => this.selected()?.role ?? null);
  readonly canManage = computed(() => {
    const r = this.myRole();
    return r != null && roleAtLeast(r, 'admin');
  });
  readonly canShare = computed(() => {
    const r = this.myRole();
    return r != null && roleAtLeast(r, 'editor');
  });

  ngOnInit(): void {
    this.refresh();
  }

  refresh(): void {
    this.loading.set(true);
    this.api.list().subscribe({
      next: (list) => {
        this.workspaces.set(list);
        this.loading.set(false);
        const current = this.selected();
        if (current) {
          const still = list.find((w) => w.id === current.id);
          if (still) this.select(still);
          else this.selected.set(null);
        }
      },
      error: () => this.loading.set(false),
    });
  }

  create(): void {
    const name = this.newWorkspaceName.trim();
    if (!name) return;
    this.api.create(name).subscribe({
      next: (ws) => {
        this.newWorkspaceName = '';
        this.workspaces.update((list) => [ws, ...list]);
        this.select(ws);
        this.notify.add('success', this.i18n.translate('ws.created'), ws.name);
      },
      error: () => this.notify.add('error', this.i18n.translate('ws.createFailed')),
    });
  }

  select(ws: Workspace): void {
    this.selected.set(ws);
    this.commentsFor.set(null);
    this.loadDetail(ws.id);
  }

  toggleComments(conv: SharedConversation): void {
    this.commentsFor.set(
      this.commentsFor()?.conversation_id === conv.conversation_id ? null : conv,
    );
  }

  private loadDetail(id: string): void {
    this.api.members(id).subscribe({ next: (m) => this.members.set(m) });
    this.api.conversations(id).subscribe({ next: (c) => this.sharedConversations.set(c) });
    this.api.documents(id).subscribe({ next: (d) => this.sharedDocuments.set(d) });

    const profileId = this.profiles.current()?.id;
    if (profileId) {
      this.conversationsApi.list(profileId).subscribe({ next: (c) => this.myConversations.set(c) });
      this.knowledgeApi.listDocuments(profileId).subscribe({ next: (d) => this.myDocuments.set(d) });
    }
  }

  rename(ws: Workspace): void {
    const name = prompt(this.i18n.translate('ws.renamePrompt'), ws.name)?.trim();
    if (!name || name === ws.name) return;
    this.api.rename(ws.id, name).subscribe({
      next: (updated) => {
        this.workspaces.update((list) => list.map((w) => (w.id === updated.id ? updated : w)));
        if (this.selected()?.id === updated.id) this.selected.set(updated);
      },
      error: () => this.notify.add('error', this.i18n.translate('ws.renameFailed')),
    });
  }

  deleteWorkspace(ws: Workspace): void {
    if (!confirm(this.i18n.translate('ws.deleteConfirm', { name: ws.name }))) return;
    this.api.delete(ws.id).subscribe({
      next: () => {
        this.workspaces.update((list) => list.filter((w) => w.id !== ws.id));
        if (this.selected()?.id === ws.id) this.selected.set(null);
        this.notify.add('success', this.i18n.translate('ws.deleted'));
      },
      error: () => this.notify.add('error', this.i18n.translate('ws.deleteFailed')),
    });
  }

  // --- members ---
  invite(): void {
    const ws = this.selected();
    const email = this.inviteEmail.trim();
    if (!ws || !email) return;
    this.api.addMember(ws.id, email, this.inviteRole).subscribe({
      next: (members) => {
        this.members.set(members);
        this.inviteEmail = '';
        this.notify.add('success', this.i18n.translate('ws.memberAdded'), email);
        this.refreshMemberCount(ws.id, members.length);
      },
      error: (err) => this.notify.add('error', this.i18n.translate('ws.addFailed'), err?.error?.detail),
    });
  }

  changeRole(member: WorkspaceMember, role: WorkspaceRole): void {
    const ws = this.selected();
    if (!ws || member.role === role) return;
    this.api.updateMemberRole(ws.id, member.user_id, role).subscribe({
      next: (members) => this.members.set(members),
      error: (err) => this.notify.add('error', this.i18n.translate('ws.roleChangeFailed'), err?.error?.detail),
    });
  }

  removeMember(member: WorkspaceMember): void {
    const ws = this.selected();
    if (!ws) return;
    if (!confirm(this.i18n.translate('ws.removeMemberConfirm', { email: member.email }))) return;
    this.api.removeMember(ws.id, member.user_id).subscribe({
      next: () => {
        this.members.update((list) => list.filter((m) => m.user_id !== member.user_id));
        this.refreshMemberCount(ws.id, this.members().length);
      },
      error: (err) => this.notify.add('error', this.i18n.translate('mcp.removeFailed'), err?.error?.detail),
    });
  }

  private refreshMemberCount(id: string, count: number): void {
    this.workspaces.update((list) =>
      list.map((w) => (w.id === id ? { ...w, member_count: count } : w)),
    );
    const sel = this.selected();
    if (sel?.id === id) this.selected.set({ ...sel, member_count: count });
  }

  // --- shared conversations ---
  shareConversation(): void {
    const ws = this.selected();
    if (!ws || !this.shareConversationId) return;
    this.api.shareConversation(ws.id, this.shareConversationId).subscribe({
      next: (list) => {
        this.sharedConversations.set(list);
        this.shareConversationId = '';
        this.notify.add('success', this.i18n.translate('ws.convShared'));
      },
      error: (err) => this.notify.add('error', this.i18n.translate('ws.shareFailed'), err?.error?.detail),
    });
  }

  unshareConversation(conversationId: string): void {
    const ws = this.selected();
    if (!ws) return;
    this.api.unshareConversation(ws.id, conversationId).subscribe({
      next: () => {
        this.sharedConversations.update((list) =>
          list.filter((c) => c.conversation_id !== conversationId),
        );
        if (this.commentsFor()?.conversation_id === conversationId) this.commentsFor.set(null);
      },
      error: () => this.notify.add('error', this.i18n.translate('ws.unshareFailed')),
    });
  }

  // --- shared documents ---
  shareDocument(): void {
    const ws = this.selected();
    if (!ws || !this.shareDocumentId) return;
    this.api.shareDocument(ws.id, this.shareDocumentId).subscribe({
      next: (list) => {
        this.sharedDocuments.set(list);
        this.shareDocumentId = '';
        this.notify.add('success', this.i18n.translate('ws.docShared'));
      },
      error: (err) => this.notify.add('error', this.i18n.translate('ws.shareFailed'), err?.error?.detail),
    });
  }

  unshareDocument(documentId: string): void {
    const ws = this.selected();
    if (!ws) return;
    this.api.unshareDocument(ws.id, documentId).subscribe({
      next: () =>
        this.sharedDocuments.update((list) => list.filter((d) => d.document_id !== documentId)),
      error: () => this.notify.add('error', this.i18n.translate('ws.unshareFailed')),
    });
  }
}
