import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

import { AppConfigService } from '../config/app-config.service';

/** Phase 18 — persistent multi-step workflows (agent runs). A durable
 *  server-side tool loop with pause/resume and per-step inspection. */

export type AgentRunStatus =
  | 'pending'
  | 'running'
  | 'paused'
  | 'completed'
  | 'failed'
  | 'cancelled';

export interface AgentRunStep {
  id: string;
  run_id: string;
  step_index: number;
  kind: 'assistant' | 'tool_call' | 'tool_result' | 'final' | 'error' | 'note';
  name?: string | null;
  content: string;
  created_at: number;
}

export interface AgentRun {
  id: string;
  profile_id: string;
  goal: string;
  model: string;
  status: AgentRunStatus;
  max_steps: number;
  current_step: number;
  result?: string | null;
  error?: string | null;
  created_at: number;
  updated_at: number;
  steps?: AgentRunStep[] | null;
}

export interface AgentRunCreate {
  goal: string;
  model: string;
  max_steps?: number;
  system_prompt?: string;
}

@Injectable({ providedIn: 'root' })
export class WorkflowService {
  private readonly http = inject(HttpClient);
  private readonly config = inject(AppConfigService);

  private get base(): string {
    return `${this.config.apiUrl}/workflows`;
  }

  create(body: AgentRunCreate): Observable<AgentRun> {
    return this.http.post<AgentRun>(this.base, body);
  }

  list(): Observable<AgentRun[]> {
    return this.http.get<AgentRun[]>(this.base);
  }

  get(id: string): Observable<AgentRun> {
    return this.http.get<AgentRun>(`${this.base}/${id}`);
  }

  pause(id: string): Observable<AgentRun> {
    return this.http.post<AgentRun>(`${this.base}/${id}/pause`, {});
  }

  resume(id: string): Observable<AgentRun> {
    return this.http.post<AgentRun>(`${this.base}/${id}/resume`, {});
  }

  cancel(id: string): Observable<AgentRun> {
    return this.http.post<AgentRun>(`${this.base}/${id}/cancel`, {});
  }

  remove(id: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/${id}`);
  }
}
