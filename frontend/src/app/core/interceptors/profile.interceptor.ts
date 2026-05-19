import { HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { ProfileService } from '../services/profile.service';

export const profileInterceptor: HttpInterceptorFn = (req, next) => {
  const profileService = inject(ProfileService);
  const profileId = profileService.currentId;

  if (!profileId || profileId === 'default') {
    return next(req);
  }

  return next(req.clone({ setHeaders: { 'X-Profile-ID': profileId } }));
};
