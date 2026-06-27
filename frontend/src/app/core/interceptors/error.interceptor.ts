import { HttpInterceptorFn, HttpErrorResponse } from '@angular/common/http';
import { inject } from '@angular/core';
import { throwError, catchError } from 'rxjs';
import { NotificationService } from '../services/notification.service';

function extractDetail(err: HttpErrorResponse): string {
  const body = err.error;
  if (!body) return `HTTP ${err.status}`;
  // FastAPI shape: { detail: { message: '...' } } or { detail: '...' }
  if (typeof body.detail === 'string') return body.detail;
  if (typeof body.detail?.message === 'string') return body.detail.message;
  if (typeof body.message === 'string') return body.message;
  return err.message || `HTTP ${err.status}`;
}

function titleForStatus(status: number): string {
  if (status === 429) return 'Rate limit exceeded';
  if (status === 400) return 'Bad request';
  if (status === 401 || status === 403) return 'Unauthorized';
  if (status === 404) return 'Not found';
  if (status === 409) return 'Già presente';
  if (status === 502 || status === 503) return 'Backend unavailable';
  if (status >= 500) return 'Server error';
  return `Error ${status}`;
}

export const errorInterceptor: HttpInterceptorFn = (req, next) => {
  const notifications = inject(NotificationService);

  return next(req).pipe(
    catchError((err: HttpErrorResponse) => {
      const title = titleForStatus(err.status);
      const detail = extractDetail(err);
      notifications.add('error', title, detail);
      return throwError(() => err);
    })
  );
};
