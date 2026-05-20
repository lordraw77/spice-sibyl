import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule, DecimalPipe } from '@angular/common';

import { StatsService } from '../../core/services/stats.service';
import { UsageStats, ProviderStats, ModelStats } from '../../core/models/chat.models';

@Component({
  selector: 'app-stats-page',
  standalone: true,
  imports: [CommonModule, DecimalPipe],
  templateUrl: './stats-page.component.html',
  styleUrl: './stats-page.component.css',
})
export class StatsPageComponent implements OnInit {
  private readonly statsService = inject(StatsService);

  stats = signal<UsageStats | null>(null);
  loading = signal(true);
  error = signal('');

  /** Keys of provider rows with the drilldown currently open. */
  expandedProviders = signal<Set<string>>(new Set());
  /** Keys of model rows with the drilldown currently open. */
  expandedModels = signal<Set<string>>(new Set());

  ngOnInit(): void {
    this.statsService.getStats().subscribe({
      next: (data) => { this.stats.set(data); this.loading.set(false); },
      error: () => { this.error.set('Impossibile caricare le statistiche.'); this.loading.set(false); },
    });
  }

  toggleProvider(key: string): void {
    this.expandedProviders.update(set => {
      const next = new Set(set);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  }

  toggleModel(key: string): void {
    this.expandedModels.update(set => {
      const next = new Set(set);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  }

  isProviderExpanded(key: string): boolean {
    return this.expandedProviders().has(key);
  }

  isModelExpanded(key: string): boolean {
    return this.expandedModels().has(key);
  }

  providerKey(p: ProviderStats): string { return p.provider ?? '__null__'; }
  modelKey(m: ModelStats): string { return `${m.model}|${m.provider}`; }

  providerLabel(p: ProviderStats): string { return p.provider || 'Sconosciuto'; }
  modelLabel(m: ModelStats): string { return (m.model || 'Sconosciuto').replace(/^.*\//, ''); }

  formatCost(value: number): string {
    if (value === 0) return '—';
    if (value < 0.0001) return '< $0.0001';
    return '$' + value.toFixed(4);
  }

  formatLatency(ms: number | null): string {
    if (ms == null) return '—';
    return ms >= 1000 ? (ms / 1000).toFixed(1) + ' s' : Math.round(ms) + ' ms';
  }

  formatTps(tps: number | null): string {
    if (tps == null) return '—';
    return tps.toFixed(1) + ' t/s';
  }
}
