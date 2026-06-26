import { Component, OnInit, OnDestroy, AfterViewInit, ElementRef, ViewChild, inject, signal } from '@angular/core';
import { CommonModule, DecimalPipe } from '@angular/common';
import { Chart, registerables } from 'chart.js';

import { StatsService } from '../../core/services/stats.service';
import { UsageStats, ProviderStats, ModelStats, DailyStats } from '../../core/models/chat.models';

Chart.register(...registerables);

@Component({
  selector: 'app-stats-page',
  standalone: true,
  imports: [CommonModule, DecimalPipe],
  templateUrl: './stats-page.component.html',
  styleUrl: './stats-page.component.css',
})
export class StatsPageComponent implements OnInit, AfterViewInit, OnDestroy {
  private readonly statsService = inject(StatsService);

  stats = signal<UsageStats | null>(null);
  loading = signal(true);
  error = signal('');

  /** Keys of provider rows with the drilldown currently open. */
  expandedProviders = signal<Set<string>>(new Set());
  /** Keys of model rows with the drilldown currently open. */
  expandedModels = signal<Set<string>>(new Set());

  /** Daily stats for charts */
  dailyStats = signal<DailyStats[]>([]);
  chartDays = signal(30);

  @ViewChild('tokensChart') tokensChartRef?: ElementRef<HTMLCanvasElement>;
  @ViewChild('costChart') costChartRef?: ElementRef<HTMLCanvasElement>;
  private tokensChart?: Chart;
  private costChart?: Chart;

  ngOnInit(): void {
    this.statsService.getStats().subscribe({
      next: (data) => { this.stats.set(data); this.loading.set(false); },
      error: () => { this.error.set('Impossibile caricare le statistiche.'); this.loading.set(false); },
    });
    this.loadDailyStats();
  }

  ngAfterViewInit(): void {
    setTimeout(() => this.renderCharts(), 500);
  }

  ngOnDestroy(): void {
    this.tokensChart?.destroy();
    this.costChart?.destroy();
  }

  setChartDays(days: number): void {
    this.chartDays.set(days);
    this.loadDailyStats();
  }

  private loadDailyStats(): void {
    this.statsService.getDailyStats(this.chartDays()).subscribe({
      next: (data) => {
        this.dailyStats.set(data);
        this.renderCharts();
      },
      error: () => {},
    });
  }

  private renderCharts(): void {
    const data = this.dailyStats();
    if (!data.length) return;

    const labels = data.map(d => d.date);
    const tokensData = data.map(d => d.total_tokens);
    const costData = data.map(d => d.estimated_cost);

    const chartOptions = {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: '#6b7485', maxTicksLimit: 8 }, grid: { color: 'rgba(255,255,255,.04)' } },
        y: { ticks: { color: '#6b7485' }, grid: { color: 'rgba(255,255,255,.04)' } },
      },
    };

    if (this.tokensChartRef) {
      this.tokensChart?.destroy();
      this.tokensChart = new Chart(this.tokensChartRef.nativeElement, {
        type: 'line',
        data: {
          labels,
          datasets: [{
            data: tokensData,
            borderColor: '#d6b279',
            backgroundColor: 'rgba(214,178,121,.12)',
            fill: true,
            tension: 0.3,
            pointRadius: 2,
          }],
        },
        options: chartOptions,
      });
    }

    if (this.costChartRef) {
      this.costChart?.destroy();
      this.costChart = new Chart(this.costChartRef.nativeElement, {
        type: 'bar',
        data: {
          labels,
          datasets: [{
            data: costData,
            backgroundColor: 'rgba(142,208,255,.4)',
            borderColor: '#8ed0ff',
            borderWidth: 1,
            borderRadius: 3,
          }],
        },
        options: chartOptions,
      });
    }
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
