import { Component, OnInit, computed, effect, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { catchError, forkJoin, map, of } from 'rxjs';

import {
  GlobalSearchResponse,
  GraphCommunity,
  GraphNodeDetail,
  GraphRagStatus,
  KbDocument,
  KbGraph,
  WikiPage,
} from '../../core/models/chat.models';
import { KnowledgeService } from '../../core/services/knowledge.service';
import { ProfileService } from '../../core/services/profile.service';
import { NotificationService } from '../../core/services/notification.service';
import { I18nService } from '../../core/i18n/i18n.service';
import { TranslatePipe } from '../../core/i18n/translate.pipe';

/** A laid-out graph node ready to render as SVG. */
interface NodeView {
  id: string;
  label: string;
  // 'community' is never rendered here (excluded server-side) but kept for
  // assignability from the widened GraphNode type.
  type: 'document' | 'section' | 'entity' | 'community';
  x: number;
  y: number;
  r: number;
}
interface EdgeView { x1: number; y1: number; x2: number; y2: number; }

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

  // wikillm: per-document wiki/graph inspector + profile-wide graph view.
  readonly expandedDocId = signal<string | null>(null);
  readonly docTab = signal<'wiki' | 'graph'>('wiki');
  readonly wikiPages = signal<WikiPage[]>([]);
  readonly panelLoading = signal(false);
  readonly reingestBusy = signal(false);

  // Graph rendering (SVG). Scope is null for the whole profile, else a doc id.
  readonly graphScope = signal<string | null>(null);
  readonly graphOpen = signal(false);
  readonly nodesView = signal<NodeView[]>([]);
  readonly edgesView = signal<EdgeView[]>([]);
  readonly selectedNode = signal<GraphNodeDetail | null>(null);

  readonly needsReingestCount = computed(
    () => this.kbDocuments().filter((d) => d.needs_reingest).length,
  );

  // Phase 28.d: GraphRAG panel — communities + global (map-reduce) search.
  readonly graphRagStatus = signal<GraphRagStatus | null>(null);
  readonly graphRagOpen = signal(false);
  readonly communities = signal<GraphCommunity[]>([]);
  readonly communitiesBusy = signal(false);
  readonly globalQuery = signal('');
  readonly globalBusy = signal(false);
  readonly globalResult = signal<GlobalSearchResponse | null>(null);

  readonly svgW = 820;
  readonly svgH = 520;

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

    const allowed = /\.(pdf|docx|pptx|xlsx|xls|csv|json|xml|html?|txt|md|markdown|epub)$/i;
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

  // ── wikillm: re-ingest, wiki inspector, knowledge graph ─────────────────────
  runReingest(): void {
    this.reingestBusy.set(true);
    this.knowledgeService.reingest(this.profileService.currentId).subscribe({
      next: (r) => {
        this.reingestBusy.set(false);
        this.notifications.add('success', this.i18n.translate('kb.reingestDone'),
          this.i18n.translate('kb.reingestDoneBody', { n: r.rebuilt, f: r.failed }));
        this.load();
      },
      error: (err: Error) => {
        this.reingestBusy.set(false);
        this.notifications.add('error', this.i18n.translate('kb.reingestFailed'), err?.message || '');
      },
    });
  }

  /** Toggle the per-document inspector and load the requested tab. */
  toggleDoc(doc: KbDocument, tab: 'wiki' | 'graph', event: Event): void {
    event.stopPropagation();
    if (this.expandedDocId() === doc.id && this.docTab() === tab) {
      this.expandedDocId.set(null);
      return;
    }
    this.expandedDocId.set(doc.id);
    this.docTab.set(tab);
    this.selectedNode.set(null);
    if (tab === 'wiki') {
      this.panelLoading.set(true);
      this.knowledgeService.getWiki(doc.id).subscribe({
        next: (pages) => { this.wikiPages.set(pages); this.panelLoading.set(false); },
        error: () => { this.wikiPages.set([]); this.panelLoading.set(false); },
      });
    } else {
      this.loadGraph(doc.id);
    }
  }

  /** Open the profile-wide knowledge graph. */
  openGlobalGraph(): void {
    this.graphOpen.set(true);
    this.expandedDocId.set(null);
    this.loadGraph(null);
  }

  closeGraph(): void {
    this.graphOpen.set(false);
    this.selectedNode.set(null);
  }

  private loadGraph(documentId: string | null): void {
    this.graphScope.set(documentId);
    this.panelLoading.set(true);
    this.selectedNode.set(null);
    this.knowledgeService.getGraph(this.profileService.currentId, documentId ?? undefined).subscribe({
      next: (g) => { this.renderGraph(g); this.panelLoading.set(false); },
      error: () => { this.nodesView.set([]); this.edgesView.set([]); this.panelLoading.set(false); },
    });
  }

  onNodeClick(id: string): void {
    this.knowledgeService.getNode(id, this.profileService.currentId).subscribe({
      next: (d) => this.selectedNode.set(d),
      error: () => {},
    });
  }

  wikiIndent(level: number): string {
    return `${Math.max(0, level - 1) * 16}px`;
  }

  /**
   * Compute a force-directed layout (Fruchterman–Reingold) and publish the
   * SVG-ready node/edge arrays. Dependency-free and deterministic-enough for a
   * one-shot render of the capped graph.
   */
  private renderGraph(g: KbGraph): void {
    const W = this.svgW, H = this.svgH;
    if (!g.nodes.length) { this.nodesView.set([]); this.edgesView.set([]); return; }

    const idx = new Map(g.nodes.map((n, i) => [n.id, i]));
    // Seed positions on a circle for a stable, non-random starting point.
    const pos = g.nodes.map((_n, i) => {
      const a = (2 * Math.PI * i) / g.nodes.length;
      return { x: W / 2 + Math.cos(a) * 180, y: H / 2 + Math.sin(a) * 140, vx: 0, vy: 0 };
    });
    const edges = g.edges.filter((e) => idx.has(e.source) && idx.has(e.target));
    const k = Math.sqrt((W * H) / g.nodes.length) * 0.55;
    const iters = 140;

    for (let it = 0; it < iters; it++) {
      for (let i = 0; i < pos.length; i++) {
        let fx = 0, fy = 0;
        for (let j = 0; j < pos.length; j++) {
          if (i === j) continue;
          const dx = pos[i].x - pos[j].x, dy = pos[i].y - pos[j].y;
          const d = Math.hypot(dx, dy) || 0.01;
          const rep = (k * k) / d;
          fx += (dx / d) * rep; fy += (dy / d) * rep;
        }
        pos[i].vx = fx; pos[i].vy = fy;
      }
      for (const e of edges) {
        const a = pos[idx.get(e.source)!], b = pos[idx.get(e.target)!];
        const dx = a.x - b.x, dy = a.y - b.y;
        const d = Math.hypot(dx, dy) || 0.01;
        const att = (d * d) / k;
        const fx = (dx / d) * att, fy = (dy / d) * att;
        a.vx -= fx; a.vy -= fy; b.vx += fx; b.vy += fy;
      }
      const temp = 12 * (1 - it / iters);
      for (const p of pos) {
        const disp = Math.hypot(p.vx, p.vy) || 0.01;
        p.x += (p.vx / disp) * Math.min(disp, temp);
        p.y += (p.vy / disp) * Math.min(disp, temp);
        p.x = Math.max(24, Math.min(W - 24, p.x));
        p.y = Math.max(24, Math.min(H - 24, p.y));
      }
    }

    const nodes: NodeView[] = g.nodes.map((n, i) => ({
      id: n.id,
      label: n.label.length > 28 ? n.label.slice(0, 27) + '…' : n.label,
      type: n.type,
      x: pos[i].x,
      y: pos[i].y,
      r: n.type === 'document' ? 11 : Math.min(5 + n.degree, 14),
    }));
    const edgeViews: EdgeView[] = edges.map((e) => ({
      x1: pos[idx.get(e.source)!].x, y1: pos[idx.get(e.source)!].y,
      x2: pos[idx.get(e.target)!].x, y2: pos[idx.get(e.target)!].y,
    }));
    this.nodesView.set(nodes);
    this.edgesView.set(edgeViews);
  }

  // ── Phase 28.d: GraphRAG — communities + global search ──────────────────────
  /** Open the GraphRAG panel and load communities + status. */
  toggleGraphRag(): void {
    const open = !this.graphRagOpen();
    this.graphRagOpen.set(open);
    if (open) this.loadGraphRag();
  }

  private loadGraphRag(): void {
    const pid = this.profileService.currentId;
    this.knowledgeService.graphStatus(pid).subscribe({
      next: (s) => this.graphRagStatus.set(s),
      error: () => this.graphRagStatus.set(null),
    });
    this.communitiesBusy.set(true);
    this.knowledgeService.listCommunities(pid).subscribe({
      next: (c) => { this.communities.set(c); this.communitiesBusy.set(false); },
      error: () => { this.communities.set([]); this.communitiesBusy.set(false); },
    });
  }

  /** Re-detect communities and refresh the list. */
  rebuildCommunities(): void {
    this.communitiesBusy.set(true);
    this.knowledgeService.rebuildCommunities(this.profileService.currentId).subscribe({
      next: (r) => {
        this.communitiesBusy.set(false);
        this.notifications.add('success', this.i18n.translate('kb.graphrag.rebuildDone'),
          this.i18n.translate('kb.graphrag.rebuildDoneBody', { n: r.communities, e: r.entities }));
        this.loadGraphRag();
      },
      error: (err: Error) => {
        this.communitiesBusy.set(false);
        this.notifications.add('error', this.i18n.translate('kb.graphrag.rebuildFailed'), err?.message || '');
      },
    });
  }

  /** Run a GraphRAG global (map-reduce) search over community summaries. */
  runGlobalSearch(): void {
    const q = this.globalQuery().trim();
    if (!q) return;
    this.globalBusy.set(true);
    this.globalResult.set(null);
    this.knowledgeService.globalSearch(q, this.profileService.currentId).subscribe({
      next: (r) => { this.globalResult.set(r); this.globalBusy.set(false); },
      error: (err: Error) => {
        this.globalBusy.set(false);
        this.notifications.add('error', this.i18n.translate('kb.graphrag.searchFailed'), err?.message || '');
      },
    });
  }
}
