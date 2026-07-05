import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';

import { OpsService, ReadyStatus, BackupInfo } from '../../core/services/ops.service';
import { ProfileService } from '../../core/services/profile.service';
import { NotificationService } from '../../core/services/notification.service';
import { Profile } from '../../core/models/chat.models';
import { I18nService } from '../../core/i18n/i18n.service';
import { TranslatePipe } from '../../core/i18n/translate.pipe';

@Component({
  selector: 'app-ops-page',
  standalone: true,
  imports: [CommonModule, TranslatePipe],
  templateUrl: './ops-page.component.html',
  styleUrls: ['./ops-page.component.css'],
})
export class OpsPageComponent implements OnInit {
  private readonly ops = inject(OpsService);
  private readonly profiles = inject(ProfileService);
  private readonly notify = inject(NotificationService);
  private readonly i18n = inject(I18nService);

  // Readiness
  readonly ready = signal<ReadyStatus | null>(null);
  readonly readyError = signal(false);

  // Metrics
  readonly activeStreams = signal<number | null>(null);
  readonly metricsError = signal(false);

  // Backups
  readonly backups = signal<BackupInfo[]>([]);
  readonly backupBusy = signal(false);

  // Export / import
  readonly profileList = signal<Profile[]>([]);
  readonly selectedProfile = signal<string>('default');
  readonly importBusy = signal(false);

  readonly metricsHref = computed(() => this.ops.metricsUrl());

  ngOnInit(): void {
    this.refreshReady();
    this.refreshMetrics();
    this.refreshBackups();
    this.profiles.list().subscribe({
      next: (list) => {
        this.profileList.set(list);
        if (list.length) this.selectedProfile.set(this.profiles.currentId);
      },
      error: () => { /* non-fatal */ },
    });
  }

  // ── Readiness ──────────────────────────────────────────────────────────────
  refreshReady(): void {
    this.ops.getReady().subscribe({
      next: (r) => { this.ready.set(r); this.readyError.set(false); },
      // 503 still carries a body, but Angular routes it to error — surface degraded.
      error: (err) => {
        this.readyError.set(true);
        const body = err?.error as ReadyStatus | undefined;
        if (body?.checks) this.ready.set(body);
      },
    });
  }

  // ── Metrics ────────────────────────────────────────────────────────────────
  refreshMetrics(): void {
    this.ops.getMetricsRaw().subscribe({
      next: (text) => {
        this.metricsError.set(false);
        const match = text.match(/^sibyl_active_sse_streams\s+([0-9.]+)/m);
        this.activeStreams.set(match ? Math.round(parseFloat(match[1])) : null);
      },
      error: () => { this.metricsError.set(true); this.activeStreams.set(null); },
    });
  }

  openMetrics(): void {
    window.open(this.ops.metricsUrl(), '_blank');
  }

  // ── Backups ────────────────────────────────────────────────────────────────
  refreshBackups(): void {
    this.ops.listBackups().subscribe({
      next: (r) => this.backups.set(r.backups ?? []),
      error: () => this.notify.add('error', 'Backup', this.i18n.translate('ops.backupListFailed')),
    });
  }

  createBackup(): void {
    this.backupBusy.set(true);
    this.ops.createBackup().subscribe({
      next: (r) => {
        this.notify.add('success', this.i18n.translate('ops.backupCreated'), r.name);
        this.refreshBackups();
        this.backupBusy.set(false);
      },
      error: () => {
        this.notify.add('error', this.i18n.translate('ops.backupFailed'));
        this.backupBusy.set(false);
      },
    });
  }

  restoreBackup(b: BackupInfo): void {
    const ok = window.confirm(
      this.i18n.translate('ops.restoreConfirm', { name: b.name })
    );
    if (!ok) return;
    this.ops.restoreBackup(b.name).subscribe({
      next: (r) => this.notify.add('warning', this.i18n.translate('ops.restoreDone'), r.note ?? r.name, 10000),
      error: () => this.notify.add('error', this.i18n.translate('ops.restoreFailed')),
    });
  }

  formatSize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }

  // ── Export / import ──────────────────────────────────────────────────────────
  onProfileChange(event: Event): void {
    this.selectedProfile.set((event.target as HTMLSelectElement).value);
  }

  exportProfile(): void {
    const pid = this.selectedProfile();
    this.ops.exportProfile(pid).subscribe({
      next: (blob) => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `profile-${pid}.zip`;
        a.click();
        URL.revokeObjectURL(url);
      },
      error: () => this.notify.add('error', this.i18n.translate('ops.exportFailed')),
    });
  }

  onImportFile(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    this.importBusy.set(true);
    this.ops.importProfile(this.selectedProfile(), file).subscribe({
      next: (r) => {
        const total = Object.values(r.counts ?? {}).reduce((a, b) => a + b, 0);
        this.notify.add('success', this.i18n.translate('ops.importDone'), this.i18n.translate('ops.rowsImported', { n: total }));
        this.importBusy.set(false);
        input.value = '';
      },
      error: (err) => {
        this.notify.add('error', this.i18n.translate('mcp.importFailed'), err?.error?.detail);
        this.importBusy.set(false);
        input.value = '';
      },
    });
  }
}
