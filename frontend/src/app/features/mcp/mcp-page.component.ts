import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { McpService, McpServer, McpConfigBundle } from '../../core/services/mcp.service';
import { NotificationService } from '../../core/services/notification.service';
import { I18nService } from '../../core/i18n/i18n.service';
import { TranslatePipe } from '../../core/i18n/translate.pipe';

@Component({
  selector: 'app-mcp-page',
  standalone: true,
  imports: [CommonModule, FormsModule, TranslatePipe],
  templateUrl: './mcp-page.component.html',
  styleUrls: ['./mcp-page.component.css'],
})
export class McpPageComponent implements OnInit {
  private readonly mcp = inject(McpService);
  private readonly notify = inject(NotificationService);
  private readonly i18n = inject(I18nService);

  readonly servers = signal<McpServer[]>([]);
  readonly loading = signal(false);
  readonly expanded = signal<Set<string>>(new Set());
  readonly testing = signal<Set<string>>(new Set());

  // Import / add form
  readonly importJson = signal('');
  readonly importEnabled = signal(true);
  readonly importBusy = signal(false);

  readonly placeholder = `{
  "mcpServers": {
    "wikillm": {
      "command": "docker",
      "args": ["run", "--rm", "-i", "lordraw/llmwiki:latest", "python", "run_stdio.py"]
    }
  }
}`;

  ngOnInit(): void {
    this.refresh(true);
  }

  refresh(probe = false): void {
    this.loading.set(true);
    this.mcp.list(probe).subscribe({
      next: (list) => { this.servers.set(list); this.loading.set(false); },
      error: () => {
        this.notify.add('error', 'MCP', this.i18n.translate('mcp.readFailed'));
        this.loading.set(false);
      },
    });
  }

  reload(): void {
    this.loading.set(true);
    this.mcp.reload().subscribe({
      next: (list) => {
        this.servers.set(list);
        this.loading.set(false);
        this.notify.add('success', 'MCP', this.i18n.translate('mcp.reloaded'));
      },
      error: () => {
        this.notify.add('error', 'MCP', this.i18n.translate('mcp.reloadFailed'));
        this.loading.set(false);
      },
    });
  }

  toggle(server: McpServer): void {
    this.mcp.setEnabled(server.id, !server.enabled).subscribe({
      next: (updated) => this.patchServer(updated),
      error: (err) => this.notify.add('error', 'MCP', err?.error?.detail ?? this.i18n.translate('mcp.updateFailed')),
    });
  }

  test(server: McpServer): void {
    this.testing.update((s) => new Set(s).add(server.id));
    this.mcp.test(server.id).subscribe({
      next: (updated) => {
        this.patchServer(updated);
        this.testing.update((s) => { const n = new Set(s); n.delete(server.id); return n; });
        if (updated.status === 'ok') {
          this.notify.add('success', 'MCP', this.i18n.translate('mcp.toolsDetected', { name: updated.name, n: updated.tools.length }));
        } else {
          this.notify.add('error', 'MCP', `${updated.name}: ${updated.error ?? this.i18n.translate('mcp.err')}`);
        }
      },
      error: () => {
        this.testing.update((s) => { const n = new Set(s); n.delete(server.id); return n; });
        this.notify.add('error', 'MCP', this.i18n.translate('mcp.testFailed'));
      },
    });
  }

  remove(server: McpServer): void {
    if (!window.confirm(this.i18n.translate('mcp.removeConfirm', { name: server.name }))) return;
    this.mcp.remove(server.id).subscribe({
      next: () => {
        this.servers.update((list) => list.filter((s) => s.id !== server.id));
        this.notify.add('success', 'MCP', this.i18n.translate('tools.removed', { name: server.name }));
      },
      error: () => this.notify.add('error', 'MCP', this.i18n.translate('mcp.removeFailed')),
    });
  }

  toggleExpand(id: string): void {
    this.expanded.update((s) => {
      const n = new Set(s);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });
  }

  isExpanded(id: string): boolean { return this.expanded().has(id); }
  isTesting(id: string): boolean { return this.testing().has(id); }

  importConfig(): void {
    let parsed: unknown;
    try {
      parsed = JSON.parse(this.importJson());
    } catch {
      this.notify.add('error', 'MCP', this.i18n.translate('mcp.invalidJson'));
      return;
    }
    // Accept either a full {"mcpServers": {...}} bundle or a bare {name: config} map.
    const obj = parsed as Record<string, unknown>;
    const bundle: McpConfigBundle =
      obj && typeof obj === 'object' && 'mcpServers' in obj
        ? (obj as unknown as McpConfigBundle)
        : { mcpServers: obj as McpConfigBundle['mcpServers'] };

    if (!bundle.mcpServers || !Object.keys(bundle.mcpServers).length) {
      this.notify.add('error', 'MCP', this.i18n.translate('mcp.noServers'));
      return;
    }
    this.importBusy.set(true);
    this.mcp.importConfig(bundle, this.importEnabled()).subscribe({
      next: (imported) => {
        this.importBusy.set(false);
        this.importJson.set('');
        this.notify.add('success', 'MCP', this.i18n.translate('mcp.imported', { n: imported.length }));
        this.refresh(true);
      },
      error: (err) => {
        this.importBusy.set(false);
        this.notify.add('error', 'MCP', err?.error?.detail ?? this.i18n.translate('mcp.importFailed'));
      },
    });
  }

  exportConfig(): void {
    this.mcp.exportConfig().subscribe({
      next: (bundle) => {
        const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'mcp.json';
        a.click();
        URL.revokeObjectURL(url);
      },
      error: () => this.notify.add('error', 'MCP', 'Export fallito'),
    });
  }

  commandLine(server: McpServer): string {
    return [server.config.command, ...(server.config.args ?? [])].join(' ');
  }

  private patchServer(updated: McpServer): void {
    this.servers.update((list) => list.map((s) => (s.id === updated.id ? updated : s)));
  }
}
