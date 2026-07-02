import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import {
  CustomTool,
  CustomToolIn,
  CustomToolsService,
} from '../../core/services/custom-tools.service';
import { NotificationService } from '../../core/services/notification.service';

/** Phase 18 — user-defined custom tools management (per profile). */
@Component({
  selector: 'app-tools-page',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './tools-page.component.html',
  styleUrls: ['./tools-page.component.css'],
})
export class ToolsPageComponent implements OnInit {
  private readonly toolsApi = inject(CustomToolsService);
  private readonly notify = inject(NotificationService);

  readonly tools = signal<CustomTool[]>([]);
  readonly loading = signal(false);
  readonly saving = signal(false);
  readonly formOpen = signal(false);
  readonly expanded = signal<Set<string>>(new Set());
  readonly testing = signal<Set<string>>(new Set());
  readonly testResults = signal<Record<string, string>>({});

  // Form model
  name = '';
  description = '';
  parametersJson = this.defaultParams();
  url = '';
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE' = 'POST';
  authType: 'none' | 'bearer' | 'header' = 'none';
  authToken = '';
  authHeaderName = '';
  authHeaderValue = '';
  timeout = 15;
  testArgsJson = '{}';

  ngOnInit(): void {
    this.refresh();
  }

  refresh(): void {
    this.loading.set(true);
    this.toolsApi.list().subscribe({
      next: (list) => { this.tools.set(list); this.loading.set(false); },
      error: () => { this.loading.set(false); },
    });
  }

  openForm(tool?: CustomTool): void {
    if (tool) {
      this.name = tool.name;
      this.description = tool.description;
      this.parametersJson = JSON.stringify(tool.parameters, null, 2);
      this.url = tool.endpoint.url;
      this.method = tool.endpoint.method;
      this.authType = tool.endpoint.auth?.type ?? 'none';
      this.authToken = tool.endpoint.auth?.token ?? '';
      this.authHeaderName = tool.endpoint.auth?.name ?? '';
      this.authHeaderValue = tool.endpoint.auth?.value ?? '';
      this.timeout = tool.endpoint.timeout ?? 15;
    } else {
      this.name = '';
      this.description = '';
      this.parametersJson = this.defaultParams();
      this.url = '';
      this.method = 'POST';
      this.authType = 'none';
      this.authToken = '';
      this.authHeaderName = '';
      this.authHeaderValue = '';
      this.timeout = 15;
    }
    this.formOpen.set(true);
  }

  closeForm(): void {
    this.formOpen.set(false);
  }

  save(): void {
    let parameters: Record<string, unknown>;
    try {
      parameters = JSON.parse(this.parametersJson || '{}');
    } catch {
      this.notify.add('error', 'Tools', 'JSON schema dei parametri non valido');
      return;
    }
    const body: CustomToolIn = {
      name: this.name.trim(),
      description: this.description.trim(),
      parameters,
      endpoint: {
        url: this.url.trim(),
        method: this.method,
        timeout: this.timeout,
        auth: {
          type: this.authType,
          token: this.authType === 'bearer' ? this.authToken : null,
          name: this.authType === 'header' ? this.authHeaderName : null,
          value: this.authType === 'header' ? this.authHeaderValue : null,
        },
      },
      enabled: true,
    };
    this.saving.set(true);
    this.toolsApi.create(body).subscribe({
      next: () => {
        this.saving.set(false);
        this.formOpen.set(false);
        this.notify.add('success', 'Tools', `Tool "${body.name}" salvato`);
        this.refresh();
      },
      error: () => this.saving.set(false),
    });
  }

  toggle(tool: CustomTool): void {
    this.toolsApi.setEnabled(tool.id, !tool.enabled).subscribe({
      next: (updated) =>
        this.tools.update((list) => list.map((t) => (t.id === updated.id ? updated : t))),
    });
  }

  remove(tool: CustomTool): void {
    if (!window.confirm(`Rimuovere il tool "${tool.name}"?`)) return;
    this.toolsApi.remove(tool.id).subscribe({
      next: () => {
        this.tools.update((list) => list.filter((t) => t.id !== tool.id));
        this.notify.add('success', 'Tools', `"${tool.name}" rimosso`);
      },
    });
  }

  test(tool: CustomTool): void {
    let args: Record<string, unknown>;
    try {
      args = JSON.parse(this.testArgsJson || '{}');
    } catch {
      this.notify.add('error', 'Tools', 'Argomenti di test non validi (JSON)');
      return;
    }
    this.testing.update((s) => new Set(s).add(tool.id));
    this.toolsApi.test(tool.id, args).subscribe({
      next: (res) => {
        this.testing.update((s) => { const n = new Set(s); n.delete(tool.id); return n; });
        this.testResults.update((r) => ({ ...r, [tool.id]: res.result }));
        this.notify.add(res.ok ? 'success' : 'error', 'Tools',
          res.ok ? `"${tool.name}" ha risposto` : `"${tool.name}": errore`);
      },
      error: () => {
        this.testing.update((s) => { const n = new Set(s); n.delete(tool.id); return n; });
      },
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

  paramNames(tool: CustomTool): string[] {
    const props = (tool.parameters as { properties?: Record<string, unknown> })?.properties;
    return props ? Object.keys(props) : [];
  }

  paramsJson(tool: CustomTool): string {
    return JSON.stringify(tool.parameters, null, 2);
  }

  private defaultParams(): string {
    return JSON.stringify(
      {
        type: 'object',
        properties: { query: { type: 'string', description: 'Testo della richiesta' } },
        required: ['query'],
      },
      null,
      2
    );
  }
}
