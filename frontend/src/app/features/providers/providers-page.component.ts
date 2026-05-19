/**
 * ProvidersPageComponent — provider management dashboard.
 *
 * Loads the provider list from /api/v1/providers and the full model catalog
 * from /api/v1/models, then shows each provider as a card with:
 *   - Configuration status (key set / missing)
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

  readonly configuredCount = computed(() => this.providers().filter(p => p.configured).length);
  readonly totalModels = computed(() => this.providers().reduce((s, p) => s + p.model_count, 0));

  ngOnInit(): void {
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
}
