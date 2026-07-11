import { inject } from '@angular/core';
import { ActivatedRouteSnapshot, CanActivateFn, Router } from '@angular/router';

import { AuthService } from '../services/auth.service';
import { FeatureService } from '../services/feature.service';

/** Allow navigation only when a session is active; otherwise send to /login. */
export const authGuard: CanActivateFn = (_route, state) => {
  const auth = inject(AuthService);
  const router = inject(Router);

  if (auth.isAuthenticated()) {
    return true;
  }
  return router.createUrlTree(['/login'], { queryParams: { redirect: state.url } });
};

/** Restrict a route to admins. */
export const adminGuard: CanActivateFn = () => {
  const auth = inject(AuthService);
  const router = inject(Router);

  if (auth.hasRole('admin')) {
    return true;
  }
  return router.createUrlTree(['/chat']);
};

/**
 * Block direct-URL access to a feature disabled by the admin toggles. The route
 * declares its key via `data: { feature: '<key>' }`; missing key = always allowed.
 */
export const featureGuard: CanActivateFn = (route: ActivatedRouteSnapshot) => {
  const features = inject(FeatureService);
  const router = inject(Router);

  const key = route.data?.['feature'] as string | undefined;
  if (features.enabled(key)) {
    return true;
  }
  return router.createUrlTree(['/chat']);
};
