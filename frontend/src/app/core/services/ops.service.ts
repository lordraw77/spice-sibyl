import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

import { AppConfigService } from '../config/app-config.service';

export interface ReadyStatus {
  status: string;
  checks: { db: boolean; providers: number };
}

export interface BackupInfo {
  name: string;
  size_bytes: number;
  created_at: number;
}

/** Phase 16 — observability & ops endpoints (admin only, except ready/metrics). */
@Injectable({ providedIn: 'root' })
export class OpsService {
  private readonly http = inject(HttpClient);
  private readonly config = inject(AppConfigService);

  private get base(): string {
    return this.config.apiUrl;
  }

  getReady(): Observable<ReadyStatus> {
    return this.http.get<ReadyStatus>(`${this.base}/ready`);
  }

  getMetricsRaw(): Observable<string> {
    return this.http.get(`${this.base}/metrics`, { responseType: 'text' });
  }

  /** Absolute URL of the raw metrics endpoint (for "open in new tab"). */
  metricsUrl(): string {
    return `${this.base}/metrics`;
  }

  listBackups(): Observable<{ backups: BackupInfo[] }> {
    return this.http.get<{ backups: BackupInfo[] }>(`${this.base}/admin/backups`);
  }

  createBackup(): Observable<{ name: string }> {
    return this.http.post<{ name: string }>(`${this.base}/admin/backup`, {});
  }

  restoreBackup(name: string): Observable<{ status: string; name: string; note?: string }> {
    return this.http.post<{ status: string; name: string; note?: string }>(
      `${this.base}/admin/restore`,
      { name }
    );
  }

  exportProfile(profileId: string): Observable<Blob> {
    return this.http.get(`${this.base}/admin/export`, {
      params: { profile_id: profileId },
      responseType: 'blob',
    });
  }

  importProfile(profileId: string, file: File): Observable<{ status: string; counts: Record<string, number> }> {
    const form = new FormData();
    form.append('file', file);
    return this.http.post<{ status: string; counts: Record<string, number> }>(
      `${this.base}/admin/import`,
      form,
      { params: { profile_id: profileId } }
    );
  }
}
