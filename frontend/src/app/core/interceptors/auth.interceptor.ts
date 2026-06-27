import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, from, switchMap, throwError } from 'rxjs';

import { AuthService } from '../services/auth.service';

/** Requests that must never carry a token or trigger a refresh loop. */
function isAuthEndpoint(url: string): boolean {
  return url.includes('/auth/login') || url.includes('/auth/refresh');
}

function withBearer(req: any, token: string | null) {
  return token ? req.clone({ setHeaders: { Authorization: `Bearer ${token}` } }) : req;
}

export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const auth = inject(AuthService);
  const router = inject(Router);

  // Leave the public config file and the login/refresh calls untouched.
  if (req.url.includes('/config/app-config.json') || isAuthEndpoint(req.url)) {
    return next(req);
  }

  return next(withBearer(req, auth.token)).pipe(
    catchError((err: HttpErrorResponse) => {
      if (err.status !== 401) {
        return throwError(() => err);
      }
      // Try a single silent refresh, then replay the original request.
      return from(auth.refresh()).pipe(
        switchMap((ok) => {
          if (!ok) {
            router.navigate(['/login']);
            return throwError(() => err);
          }
          return next(withBearer(req, auth.token));
        })
      );
    })
  );
};
