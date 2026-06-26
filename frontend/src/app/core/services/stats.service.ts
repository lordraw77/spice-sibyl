import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { AppConfigService } from '../config/app-config.service';
import { DailyStats, UsageStats } from '../models/chat.models';

@Injectable({ providedIn: 'root' })
export class StatsService {
  private readonly http = inject(HttpClient);
  private readonly config = inject(AppConfigService);

  getStats(): Observable<UsageStats> {
    return this.http.get<UsageStats>(`${this.config.apiUrl}/stats`);
  }

  getDailyStats(days: number = 30, profileId?: string): Observable<DailyStats[]> {
    const params: Record<string, string> = { days: days.toString() };
    if (profileId) params['profile_id'] = profileId;
    return this.http.get<DailyStats[]>(`${this.config.apiUrl}/stats/daily`, { params });
  }
}
