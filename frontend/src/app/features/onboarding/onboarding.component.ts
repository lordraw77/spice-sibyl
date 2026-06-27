import { Component, HostListener, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { OnboardingService } from '../../core/services/onboarding.service';

interface TourStep {
  /** CSS selector of the element to spotlight (via [data-tour]). */
  selector: string;
  title: string;
  body: string;
}

interface SpotlightRect {
  top: number;
  left: number;
  width: number;
  height: number;
}

/**
 * First-run guided tour. Renders a dimming overlay with a spotlight cut-out
 * around each target element (located by its `data-tour` attribute) plus a
 * tooltip card. When a target is missing/offscreen — or on narrow viewports
 * where sidebar targets aren't reliably visible — it falls back to a centered
 * card with the same copy. Styling uses the app's theme CSS custom properties.
 */
@Component({
  selector: 'app-onboarding',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="tour-backdrop" (click)="skip()">
      <!-- Spotlight cut-out (desktop, when target located) -->
      <div
        *ngIf="spotlight() as rect"
        class="tour-spotlight"
        [style.top.px]="rect.top"
        [style.left.px]="rect.left"
        [style.width.px]="rect.width"
        [style.height.px]="rect.height"
      ></div>

      <!-- Tooltip / card -->
      <div
        class="tour-card"
        [class.centered]="!spotlight()"
        [style.top.px]="cardPos()?.top"
        [style.left.px]="cardPos()?.left"
        (click)="$event.stopPropagation()"
      >
        <div class="tour-progress">{{ index() + 1 }} / {{ steps.length }}</div>
        <h3 class="tour-title">{{ current().title }}</h3>
        <p class="tour-body">{{ current().body }}</p>
        <div class="tour-actions">
          <button class="tour-skip" (click)="skip()">Salta</button>
          <div class="tour-nav">
            <button class="tour-btn" *ngIf="index() > 0" (click)="prev()">Indietro</button>
            <button class="tour-btn primary" (click)="next()">
              {{ index() === steps.length - 1 ? 'Fine' : 'Avanti' }}
            </button>
          </div>
        </div>
      </div>
    </div>
  `,
  styles: [`
    .tour-backdrop {
      position: fixed;
      inset: 0;
      z-index: 1000;
      background: rgba(0, 0, 0, .55);
    }
    .tour-spotlight {
      position: fixed;
      border-radius: .6rem;
      box-shadow: 0 0 0 9999px rgba(0, 0, 0, .55), 0 0 0 3px var(--accent);
      background: transparent;
      pointer-events: none;
      transition: top .2s ease, left .2s ease, width .2s ease, height .2s ease;
    }
    .tour-card {
      position: fixed;
      width: min(320px, 86vw);
      background: var(--bg-surface);
      border: 1px solid var(--border-light);
      border-radius: .75rem;
      padding: 1rem 1.1rem 1.1rem;
      box-shadow: 0 8px 28px var(--shadow);
      color: var(--text-primary);
      z-index: 1001;
    }
    .tour-card.centered {
      top: 50% !important;
      left: 50% !important;
      transform: translate(-50%, -50%);
    }
    .tour-progress {
      font-size: .72rem;
      color: var(--accent);
      font-weight: 600;
      margin-bottom: .35rem;
    }
    .tour-title {
      font-size: 1.05rem;
      font-weight: 700;
      margin: 0 0 .4rem;
      color: var(--text-primary);
    }
    .tour-body {
      font-size: .86rem;
      line-height: 1.45;
      color: var(--text-secondary);
      margin: 0 0 1rem;
    }
    .tour-actions {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: .5rem;
    }
    .tour-nav { display: flex; gap: .4rem; }
    .tour-btn, .tour-skip {
      font-size: .82rem;
      padding: .4rem .85rem;
      border-radius: .5rem;
      border: 1px solid var(--border);
      background: transparent;
      color: var(--text-tertiary);
      cursor: pointer;
      transition: background .15s, color .15s, border-color .15s;
    }
    .tour-btn:hover, .tour-skip:hover { background: var(--bg-surface-hover); color: var(--text-primary); }
    .tour-btn.primary {
      background: var(--accent-bg);
      border-color: var(--accent-border);
      color: var(--accent);
      font-weight: 600;
    }
    .tour-btn.primary:hover { background: var(--accent-bg); color: var(--accent); filter: brightness(1.1); }
  `]
})
export class OnboardingComponent implements OnInit {
  private readonly onboarding = inject(OnboardingService);

  readonly steps: TourStep[] = [
    {
      selector: '[data-tour="model"]',
      title: 'Scegli il modello',
      body: 'Seleziona qui il provider e il modello LLM con cui vuoi chattare. Puoi filtrare per capacità (es. vision) e vedere quali sono gratuiti.',
    },
    {
      selector: '[data-tour="tools"]',
      title: 'Strumenti (tool calling)',
      body: 'Attiva i tool — calcolatrice, ricerca web, data/ora e altro — per dare super-poteri al modello durante la conversazione.',
    },
    {
      selector: '[data-tour="system-prompt"]',
      title: 'Istruzioni di sistema',
      body: 'Imposta un prompt di sistema persistente per guidare tono, ruolo e comportamento dell’assistente.',
    },
    {
      selector: '[data-tour="composer"]',
      title: 'Comandi rapidi',
      body: 'Scrivi qui il tuo messaggio. Digita "/" per i comandi rapidi (/imagine, /new, /model…) e usa 📎 per allegare immagini.',
    },
  ];

  readonly index = signal(0);
  readonly current = computed(() => this.steps[this.index()]);
  readonly spotlight = signal<SpotlightRect | null>(null);
  readonly cardPos = signal<{ top: number; left: number } | null>(null);

  ngOnInit(): void {
    this.locate();
  }

  @HostListener('window:resize')
  @HostListener('window:scroll')
  onViewportChange(): void {
    this.locate();
  }

  /** Compute the spotlight rect + card position for the current step. */
  private locate(): void {
    const step = this.current();
    const el = document.querySelector(step.selector) as HTMLElement | null;
    const rect = el?.getBoundingClientRect();
    const narrow = window.innerWidth < 992;

    if (!rect || rect.width === 0 || rect.height === 0 || narrow) {
      // Fallback: centered card, no spotlight.
      this.spotlight.set(null);
      this.cardPos.set(null);
      return;
    }

    const pad = 6;
    const spot: SpotlightRect = {
      top: rect.top - pad,
      left: rect.left - pad,
      width: rect.width + pad * 2,
      height: rect.height + pad * 2,
    };
    this.spotlight.set(spot);

    // Place the card below the target if there's room, otherwise above.
    const cardWidth = Math.min(320, window.innerWidth * 0.86);
    const estCardHeight = 200;
    let top = spot.top + spot.height + 12;
    if (top + estCardHeight > window.innerHeight) {
      top = Math.max(12, spot.top - estCardHeight - 12);
    }
    let left = spot.left;
    if (left + cardWidth > window.innerWidth - 12) {
      left = window.innerWidth - cardWidth - 12;
    }
    this.cardPos.set({ top, left: Math.max(12, left) });
  }

  next(): void {
    if (this.index() >= this.steps.length - 1) {
      this.onboarding.complete();
      return;
    }
    this.index.update(i => i + 1);
    this.locate();
  }

  prev(): void {
    if (this.index() > 0) {
      this.index.update(i => i - 1);
      this.locate();
    }
  }

  skip(): void {
    this.onboarding.skip();
  }
}
