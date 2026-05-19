/**
 * ProvidersPageComponent — provider management dashboard.
 *
 * Loads the provider list from /api/v1/providers and the full model catalog
 * from /api/v1/models, then shows each provider as a card with:
 *   - Enable/disable toggle
 *   - Configuration status (key set / missing)
 *   - Inline API key input form
 *   - Model count and capabilities
 *   - "Test Connection" button with live result
 *   - Expandable model list
 */
import { Component, OnInit, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { forkJoin } from 'rxjs';

import { ChatService } from '../../core/services/chat.service';
import { ChatModel, ProviderStatus, ProviderTestResult } from '../../core/models/chat.models';

interface TestState {
  testing: boolean;
  result: ProviderTestResult | null;
}

interface KeyFormState {
  open: boolean;
  value: string;
  saving: boolean;
  error: string;
}

@Component({
  selector: 'app-providers-page',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './providers-page.component.html',
  styleUrl: './providers-page.component.css',
})
export class ProvidersPageComponent implements OnInit {
  private readonly chatService = inject(ChatService);

  providers = signal<ProviderStatus[]>([]);
  modelsByProvider = signal<Record<string, ChatModel[]>>({});
  loading = signal(true);
  error = signal('');
  expandedProviders = signal<Set<string>>(new Set());
  testStates = signal<Record<string, TestState>>({});
  keyForms = signal<Record<string, KeyFormState>>({});

  readonly configuredCount = computed(() => this.providers().filter(p => p.configured).length);
  readonly totalModels = computed(() => this.providers().reduce((s, p) => s + p.model_count, 0));

  ngOnInit(): void {
    this.loadData();
  }

  private loadData(): void {
    this.loading.set(true);
    forkJoin({
      providers: this.chatService.providerStatuses(),
      models: this.chatService.models(),
    }).subscribe({
      next: ({ providers, models }) => {
        this.providers.set(providers);
        const grouped: Record<string, ChatModel[]> = {};
        for (const model of models.data) {
          const p = model.provider ?? 'unknown';
          (grouped[p] ??= []).push(model);
        }
        this.modelsByProvider.set(grouped);
        this.loading.set(false);
      },
      error: () => {
        this.error.set('Could not load providers from the backend.');
        this.loading.set(false);
      },
    });
  }

  toggleExpanded(providerId: string): void {
    const next = new Set(this.expandedProviders());
    next.has(providerId) ? next.delete(providerId) : next.add(providerId);
    this.expandedProviders.set(next);
  }

  isExpanded(providerId: string): boolean {
    return this.expandedProviders().has(providerId);
  }

  testProvider(providerId: string): void {
    this.testStates.update(s => ({ ...s, [providerId]: { testing: true, result: null } }));
    this.chatService.testProvider(providerId).subscribe({
      next: result => this.testStates.update(s => ({ ...s, [providerId]: { testing: false, result } })),
      error: () => this.testStates.update(s => ({
        ...s,
        [providerId]: { testing: false, result: { provider_id: providerId, ok: false, latency_ms: null, model_count: null, error: 'Request failed' } },
      })),
    });
  }

  getTestState(providerId: string): TestState {
    return this.testStates()[providerId] ?? { testing: false, result: null };
  }

  getModels(providerId: string): ChatModel[] {
    return this.modelsByProvider()[providerId] ?? [];
  }

  toggleEnabled(provider: ProviderStatus): void {
    const next = !provider.enabled;
    this.chatService.updateProvider(provider.id, next).subscribe({
      next: updated => {
        this.providers.update(list => list.map(p => p.id === updated.id ? updated : p));
      },
      error: () => {
        // revert optimistic UI — reload from backend
        this.loadData();
      },
    });
    // optimistic update
    this.providers.update(list => list.map(p => p.id === provider.id ? { ...p, enabled: next } : p));
  }

  setKeyValue(providerId: string, value: string): void {
    this.keyForms.update(s => ({ ...s, [providerId]: { ...this.getKeyForm(providerId), value } }));
  }

  openKeyForm(providerId: string): void {
    this.keyForms.update(s => ({
      ...s,
      [providerId]: { open: true, value: '', saving: false, error: '' },
    }));
  }

  closeKeyForm(providerId: string): void {
    this.keyForms.update(s => ({ ...s, [providerId]: { open: false, value: '', saving: false, error: '' } }));
  }

  getKeyForm(providerId: string): KeyFormState {
    return this.keyForms()[providerId] ?? { open: false, value: '', saving: false, error: '' };
  }

  saveKey(providerId: string): void {
    const form = this.getKeyForm(providerId);
    if (!form.value.trim()) return;
    this.keyForms.update(s => ({ ...s, [providerId]: { ...form, saving: true, error: '' } }));
    this.chatService.setProviderKey(providerId, form.value.trim()).subscribe({
      next: () => {
        this.closeKeyForm(providerId);
        this.loadData();
      },
      error: () => {
        this.keyForms.update(s => ({
          ...s,
          [providerId]: { ...this.getKeyForm(providerId), saving: false, error: 'Failed to save key.' },
        }));
      },
    });
  }
}
