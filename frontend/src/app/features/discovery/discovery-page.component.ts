/**
 * DiscoveryPageComponent — provider model catalog browser.
 *
 * Allows the user to trigger a live model-catalog fetch for either
 * Cloudflare Workers AI or OpenRouter, then browse the returned model list
 * and copy the generated YAML config block into provider_models.yaml.
 *
 * The YAML editor is a textarea overlaid with a syntax-highlighted read-only
 * layer (highlight layer) that scrolls in sync with the textarea so edits
 * stay visually aligned with the highlighting.
 */
import { Component, ElementRef, ViewChild, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { DiscoveryService, DiscoveryModel } from '../../core/services/discovery.service';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';

type DiscoverySource = 'cloudflare' | 'openrouter' | 'gemini' | 'groq' | 'cerebras' | 'mistral' | 'nvidia' | 'ollama';

@Component({
  selector: 'app-discovery-page',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './discovery-page.component.html',
  styleUrl: './discovery-page.component.css',
})
export class DiscoveryPageComponent {
  private readonly discoveryService = inject(DiscoveryService);
  private readonly sanitizer = inject(DomSanitizer);

  @ViewChild('editorTextarea') private editorTextarea?: ElementRef<HTMLTextAreaElement>;
  @ViewChild('highlightLayer') private highlightLayer?: ElementRef<HTMLDivElement>;

  activeSource = signal<DiscoverySource>('cloudflare');
  loading = signal(false);
  modelCount = signal<number | null>(null);
  yamlContent = signal('');
  models = signal<DiscoveryModel[]>([]);
  highlightedYaml = signal<SafeHtml>('');
  /** 'idle' | 'success' | 'error' — drives the copy-button label and color */
  copyState = signal<'idle' | 'success' | 'error'>('idle');

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
    }
  });

  pageSubtitle = computed(() => {
    switch (this.activeSource()) {
      case 'cloudflare':
        return 'Recupera e visualizza tutti i modelli Text Generation disponibili sul tuo account Cloudflare Workers AI.';
      case 'openrouter':
        return 'Recupera e visualizza i modelli chat disponibili via OpenRouter e genera il blocco YAML per il catalogo.';
      case 'gemini':
        return 'Recupera e visualizza tutti i modelli generateContent disponibili tramite la Google Generative AI API.';
      case 'groq':
        return 'Recupera e visualizza tutti i modelli LLM disponibili sulla piattaforma Groq e genera il blocco YAML per il catalogo.';
      case 'cerebras':
        return 'Recupera e visualizza tutti i modelli LLM disponibili sulla piattaforma Cerebras e genera il blocco YAML per il catalogo.';
      case 'mistral':
        return 'Recupera e visualizza tutti i modelli chat disponibili su Mistral AI e genera il blocco YAML per il catalogo.';
      case 'nvidia':
        return 'Recupera e visualizza tutti i modelli LLM disponibili su NVIDIA NIM (integrate.api.nvidia.com) e genera il blocco YAML per il catalogo.';
      case 'ollama':
        return 'Recupera e visualizza tutti i modelli scaricati nell\'istanza Ollama locale e genera il blocco YAML per il catalogo.';
    }
  });

  freeModelCount = computed(() => this.models().filter((m) => m.free).length);
  uniqueCapabilityCount = computed(() => {
    const caps = this.models().flatMap((m) => m.capabilities || []);
    return new Set(caps).size;
  });

  /** Switch between Cloudflare and OpenRouter sources, resetting all state. */
  setSource(source: DiscoverySource): void {
    if (this.activeSource() === source) return;
    this.activeSource.set(source);
    this.resetState();
  }

  /** Trigger the discovery API call for the active source. */
  run(): void {
    this.loading.set(true);
    this.yamlContent.set('');
    this.highlightedYaml.set('');
    this.models.set([]);
    this.modelCount.set(null);
    this.copyState.set('idle');

    const source = this.activeSource();
    const request$ =
      source === 'cloudflare' ? this.discoveryService.runCloudflareDiscovery()
      : source === 'openrouter' ? this.discoveryService.runOpenRouterDiscovery()
      : source === 'gemini' ? this.discoveryService.runGeminiDiscovery()
      : source === 'groq' ? this.discoveryService.runGroqDiscovery()
      : source === 'cerebras' ? this.discoveryService.runCerebrasDiscovery()
      : source === 'mistral' ? this.discoveryService.runMistralDiscovery()
      : source === 'nvidia' ? this.discoveryService.runNvidiaDiscovery()
      : this.discoveryService.runOllamaDiscovery();

    request$.subscribe({
      next: (result) => {
        this.modelCount.set(result.model_count);
        this.yamlContent.set(result.yaml);
        this.models.set(result.models);
        this.highlightedYaml.set(this.highlightYaml(result.yaml));
        this.loading.set(false);
        // Sync the highlight layer scroll after the DOM has updated
        queueMicrotask(() => this.syncEditorScroll(true));
      },
      error: () => {
        this.loading.set(false);
      },
    });
  }

  /** Keep the YAML signal and syntax highlighting in sync as the user edits. */
  onYamlChange(value: string): void {
    this.yamlContent.set(value);
    this.highlightedYaml.set(this.highlightYaml(value));
    this.copyState.set('idle');
    queueMicrotask(() => this.syncEditorScroll());
  }

  /** Copy the YAML content to the clipboard, falling back to execCommand for older browsers. */
  async copyYaml(): Promise<void> {
    const text = this.yamlContent();
    if (!text) {
      this.copyState.set('error');
      return;
    }

    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        this.copyWithFallback(text);
      }
      this.copyState.set('success');
    } catch {
      try {
        this.copyWithFallback(text);
        this.copyState.set('success');
      } catch {
        this.copyState.set('error');
      }
    }

    // Reset button state after 1.8 s
    window.setTimeout(() => this.copyState.set('idle'), 1800);
  }

  /** Mirror the textarea's scroll position onto the highlight layer so they stay aligned. */
  syncEditorScroll(reset = false): void {
    const textarea = this.editorTextarea?.nativeElement;
    const highlight = this.highlightLayer?.nativeElement;

    if (!textarea || !highlight) {
      return;
    }

    if (reset) {
      textarea.scrollTop = 0;
      textarea.scrollLeft = 0;
    }

    highlight.scrollTop = textarea.scrollTop;
    highlight.scrollLeft = textarea.scrollLeft;
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
    this.yamlContent.set('');
    this.models.set([]);
    this.highlightedYaml.set('');
    this.copyState.set('idle');
  }

  /**
   * Clipboard fallback for browsers without navigator.clipboard support.
   * Creates a temporary off-screen textarea, selects its content, and
   * invokes the deprecated execCommand('copy').
   */
  private copyWithFallback(text: string): void {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    textarea.style.pointerEvents = 'none';
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    textarea.setSelectionRange(0, textarea.value.length);
    const ok = document.execCommand('copy');
    document.body.removeChild(textarea);
    if (!ok) {
      throw new Error('execCommand copy failed');
    }
  }

  /**
   * Apply lightweight YAML syntax highlighting using CSS span classes.
   *
   * CSS classes used:
   *   .yk   — YAML key
   *   .yc   — colon
   *   .yb   — boolean value
   *   .yn   — numeric value
   *   .ycmt — comment
   *   .yarr — inline array
   *   .ys   — string value
   */
  private highlightYaml(yaml: string): SafeHtml {
    const escaped = yaml
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');

    const highlighted = escaped
      .replace(/^(\s*)([\w_-]+)(:)(?=\s|$)/gm, '$1<span class="yk">$2</span><span class="yc">$3</span>')
      .replace(/:\s*(true|false)\b/g, ': <span class="yb">$1</span>')
      .replace(/:\s*(\d+(?:\.\d+)?)\b/g, ': <span class="yn">$1</span>')
      .replace(/(^|\s)(#.*)$/gm, '$1<span class="ycmt">$2</span>')
      .replace(/:\s*(\[.*?\])/g, ': <span class="yarr">$1</span>')
      .replace(/:\s*([^<\n\[]+?)(<\/span>)?(\n|$)/g, (match, val, cls, end) => {
        if (cls) return match;
        const trimmed = val.trim();
        if (!trimmed || trimmed === 'true' || trimmed === 'false') return match;
        if (/^\d/.test(trimmed)) return match;
        if (trimmed.startsWith('[')) return match;
        return `: <span class="ys">${val}</span>${end}`;
      });

    return this.sanitizer.bypassSecurityTrustHtml(highlighted);
  }
}