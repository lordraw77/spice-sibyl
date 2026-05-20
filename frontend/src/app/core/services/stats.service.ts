import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { AppConfigService } from '../config/app-config.service';
import { UsageStats } from '../models/chat.models';

@Injectable({ providedIn: 'root' })
export class StatsService {
  private readonly http = inject(HttpClient);
  private readonly config = inject(AppConfigService);

  getStats(): Observable<UsageStats> {
    return this.http.get<UsageStats>(`${this.config.apiUrl}/stats`);
  }
}
