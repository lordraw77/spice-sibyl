import { Component, ElementRef, ViewChild, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { DiscoveryService, DiscoveryModel } from '../../core/services/discovery.service';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';

type DiscoverySource = 'cloudflare' | 'openrouter';

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
  error = signal('');
  highlightedYaml = signal<SafeHtml>('');
  copyState = signal<'idle' | 'success' | 'error'>('idle');

  pageTitle = computed(() =>
    this.activeSource() === 'cloudflare'
      ? '☁ Cloudflare Model Discovery'
      : '🧭 OpenRouter Model Discovery'
  );

  pageSubtitle = computed(() =>
    this.activeSource() === 'cloudflare'
      ? 'Recupera e visualizza tutti i modelli Text Generation disponibili sul tuo account Cloudflare Workers AI.'
      : 'Recupera e visualizza i modelli chat disponibili via OpenRouter e genera il blocco YAML pronto per il catalogo provider.'
  );

  freeModelCount = computed(() => this.models().filter((m) => m.free).length);
  uniqueCapabilityCount = computed(() => {
    const caps = this.models().flatMap((m) => m.capabilities || []);
    return new Set(caps).size;
  });

  setSource(source: DiscoverySource): void {
    if (this.activeSource() === source) return;
    this.activeSource.set(source);
    this.resetState();
  }

  run(): void {
    this.loading.set(true);
    this.error.set('');
    this.yamlContent.set('');
    this.highlightedYaml.set('');
    this.models.set([]);
    this.modelCount.set(null);
    this.copyState.set('idle');

    const request$ =
      this.activeSource() === 'cloudflare'
        ? this.discoveryService.runCloudflareDiscovery()
        : this.discoveryService.runOpenRouterDiscovery();

    request$.subscribe({
      next: (result) => {
        this.modelCount.set(result.model_count);
        this.yamlContent.set(result.yaml);
        this.models.set(result.models);
        this.highlightedYaml.set(this.highlightYaml(result.yaml));
        this.loading.set(false);
        queueMicrotask(() => this.syncEditorScroll(true));
      },
      error: (err) => {
        this.error.set(
          err?.error?.detail ||
          `Errore durante la discovery ${this.activeSource() === 'cloudflare' ? 'Cloudflare' : 'OpenRouter'}.`
        );
        this.loading.set(false);
      },
    });
  }

  onYamlChange(value: string): void {
    this.yamlContent.set(value);
    this.highlightedYaml.set(this.highlightYaml(value));
    this.copyState.set('idle');
    queueMicrotask(() => this.syncEditorScroll());
  }

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

    window.setTimeout(() => this.copyState.set('idle'), 1800);
  }

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
    this.error.set('');
    this.highlightedYaml.set('');
    this.copyState.set('idle');
  }

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