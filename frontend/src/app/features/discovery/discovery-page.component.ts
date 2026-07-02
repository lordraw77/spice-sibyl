/**
 * DiscoveryPageComponent — provider model catalog browser.
 *
 * Lets the user trigger a live model-catalog fetch for a provider; the
 * backend persists the result in the discovered-models catalog and the page
 * shows the saved model list. No manual YAML editing is involved anymore.
 */
import { Component, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { DiscoveryService, DiscoveryModel } from '../../core/services/discovery.service';

type DiscoverySource = 'cloudflare' | 'openrouter' | 'gemini' | 'groq' | 'cerebras' | 'mistral' | 'nvidia' | 'ollama' | 'agent';

@Component({
  selector: 'app-discovery-page',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './discovery-page.component.html',
  styleUrl: './discovery-page.component.css',
})
export class DiscoveryPageComponent {
  private readonly discoveryService = inject(DiscoveryService);

  readonly sources: DiscoverySource[] = [
    'cloudflare', 'openrouter', 'gemini', 'groq', 'cerebras', 'mistral', 'nvidia', 'ollama', 'agent',
  ];

  activeSource = signal<DiscoverySource>('cloudflare');
  loading = signal(false);
  modelCount = signal<number | null>(null);
  models = signal<DiscoveryModel[]>([]);
  savedAt = signal<number | null>(null);
  errorMessage = signal<string | null>(null);

  sourceLabel(source: DiscoverySource): string {
    switch (source) {
      case 'cloudflare': return 'Cloudflare';
      case 'openrouter': return 'OpenRouter';
      case 'gemini':     return 'Gemini';
      case 'groq':       return 'Groq';
      case 'cerebras':   return 'Cerebras';
      case 'mistral':    return 'Mistral';
      case 'nvidia':     return 'NVIDIA';
      case 'ollama':     return 'Ollama';
      case 'agent':      return 'Agent';
    }
  }

  pageTitle = computed(() => {
    switch (this.activeSource()) {
      case 'cloudflare': return '☁ Cloudflare Model Discovery';
      case 'openrouter': return '🧭 OpenRouter Model Discovery';
      case 'gemini':     return '✦ Gemini Model Discovery';
      case 'groq':       return '⚡ Groq Model Discovery';
      case 'cerebras':   return '🧠 Cerebras Model Discovery';
      case 'mistral':    return '🔶 Mistral AI Model Discovery';
      case 'nvidia':     return '⬛ NVIDIA Model Discovery';
      case 'ollama':     return '🦙 Ollama Model Discovery';
      case 'agent':      return '🤖 Agent (Multi-MCP) Discovery';
    }
  });

  pageSubtitle = computed(() => {
    switch (this.activeSource()) {
      case 'cloudflare':
        return 'Recupera tutti i modelli Text Generation del tuo account Cloudflare Workers AI e li salva nel catalogo.';
      case 'openrouter':
        return 'Recupera i modelli chat disponibili via OpenRouter e li salva nel catalogo.';
      case 'gemini':
        return 'Recupera tutti i modelli generateContent della Google Generative AI API e li salva nel catalogo.';
      case 'groq':
        return 'Recupera tutti i modelli LLM della piattaforma Groq e li salva nel catalogo.';
      case 'cerebras':
        return 'Recupera tutti i modelli LLM della piattaforma Cerebras e li salva nel catalogo.';
      case 'mistral':
        return 'Recupera tutti i modelli chat di Mistral AI e li salva nel catalogo.';
      case 'nvidia':
        return 'Recupera tutti i modelli LLM di NVIDIA NIM (integrate.api.nvidia.com) e li salva nel catalogo.';
      case 'ollama':
        return 'Recupera tutti i modelli scaricati nell\'istanza Ollama locale e li salva nel catalogo.';
      case 'agent':
        return 'Recupera i modelli esposti dal sidecar Multi-MCP e li salva nel catalogo.';
    }
  });

  freeModelCount = computed(() => this.models().filter((m) => m.free).length);
  uniqueCapabilityCount = computed(() => {
    const caps = this.models().flatMap((m) => m.capabilities || []);
    return new Set(caps).size;
  });
  savedAtLabel = computed(() => {
    const ts = this.savedAt();
    return ts ? new Date(ts * 1000).toLocaleString() : '';
  });

  /** Switch the active provider, resetting all state. */
  setSource(source: DiscoverySource): void {
    if (this.activeSource() === source) return;
    this.activeSource.set(source);
    this.resetState();
  }

  /** Trigger the discovery API call for the active provider; the backend saves the result. */
  run(): void {
    this.loading.set(true);
    this.models.set([]);
    this.modelCount.set(null);
    this.savedAt.set(null);
    this.errorMessage.set(null);

    this.discoveryService.runDiscovery(this.activeSource()).subscribe({
      next: (result) => {
        this.modelCount.set(result.model_count);
        this.models.set(result.models);
        this.savedAt.set(result.discovered_at);
        this.loading.set(false);
      },
      error: (err) => {
        this.errorMessage.set(err?.error?.detail ?? 'Discovery fallita. Riprova.');
        this.loading.set(false);
      },
    });
  }

  trackByModelId(_: number, model: DiscoveryModel): string {
    return model.id;
  }

  trackByCapability(_: number, capability: string): string {
    return capability;
  }

  private resetState(): void {
    this.loading.set(false);
    this.modelCount.set(null);
    this.models.set([]);
    this.savedAt.set(null);
    this.errorMessage.set(null);
  }
}
