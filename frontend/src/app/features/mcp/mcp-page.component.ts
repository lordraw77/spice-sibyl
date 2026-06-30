import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { McpService, McpServer, McpConfigBundle } from '../../core/services/mcp.service';
import { NotificationService } from '../../core/services/notification.service';

@Component({
  selector: 'app-mcp-page',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './mcp-page.component.html',
  styleUrls: ['./mcp-page.component.css'],
})
export class McpPageComponent implements OnInit {
  private readonly mcp = inject(McpService);
  private readonly notify = inject(NotificationService);

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
        this.notify.add('error', 'MCP', 'Impossibile leggere i server MCP');
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
        this.notify.add('success', 'MCP', 'Server ricaricati e re-interrogati');
      },
      error: () => {
        this.notify.add('error', 'MCP', 'Reload fallito');
        this.loading.set(false);
      },
    });
  }

  toggle(server: McpServer): void {
    this.mcp.setEnabled(server.id, !server.enabled).subscribe({
      next: (updated) => this.patchServer(updated),
      error: (err) => this.notify.add('error', 'MCP', err?.error?.detail ?? 'Aggiornamento fallito'),
    });
  }

  test(server: McpServer): void {
    this.testing.update((s) => new Set(s).add(server.id));
    this.mcp.test(server.id).subscribe({
      next: (updated) => {
        this.patchServer(updated);
        this.testing.update((s) => { const n = new Set(s); n.delete(server.id); return n; });
        if (updated.status === 'ok') {
          this.notify.add('success', 'MCP', `${updated.name}: ${updated.tools.length} tool rilevati`);
        } else {
          this.notify.add('error', 'MCP', `${updated.name}: ${updated.error ?? 'errore'}`);
        }
      },
      error: () => {
        this.testing.update((s) => { const n = new Set(s); n.delete(server.id); return n; });
        this.notify.add('error', 'MCP', 'Test fallito');
      },
    });
  }

  remove(server: McpServer): void {
    if (!window.confirm(`Rimuovere il server MCP "${server.name}"?`)) return;
    this.mcp.remove(server.id).subscribe({
      next: () => {
        this.servers.update((list) => list.filter((s) => s.id !== server.id));
        this.notify.add('success', 'MCP', `"${server.name}" rimosso`);
      },
      error: () => this.notify.add('error', 'MCP', 'Rimozione fallita'),
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
      this.notify.add('error', 'MCP', 'JSON non valido');
      return;
    }
    // Accept either a full {"mcpServers": {...}} bundle or a bare {name: config} map.
    const obj = parsed as Record<string, unknown>;
    const bundle: McpConfigBundle =
      obj && typeof obj === 'object' && 'mcpServers' in obj
        ? (obj as unknown as McpConfigBundle)
        : { mcpServers: obj as McpConfigBundle['mcpServers'] };

    if (!bundle.mcpServers || !Object.keys(bundle.mcpServers).length) {
      this.notify.add('error', 'MCP', 'Nessun server in "mcpServers"');
      return;
    }
    this.importBusy.set(true);
    this.mcp.importConfig(bundle, this.importEnabled()).subscribe({
      next: (imported) => {
        this.importBusy.set(false);
        this.importJson.set('');
        this.notify.add('success', 'MCP', `${imported.length} server importati`);
        this.refresh(true);
      },
      error: (err) => {
        this.importBusy.set(false);
        this.notify.add('error', 'MCP', err?.error?.detail ?? 'Import fallito');
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
