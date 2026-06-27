import { APP_INITIALIZER, isDevMode } from '@angular/core';
import { bootstrapApplication } from '@angular/platform-browser';
import { provideHttpClient, withFetch, withInterceptors } from '@angular/common/http';
import { provideRouter } from '@angular/router';
import { provideServiceWorker } from '@angular/service-worker';

import { AppComponent } from './app/app.component';
import { routes } from './app/app.routes';
import { AppConfigService } from './app/core/config/app-config.service';
import { AuthService } from './app/core/services/auth.service';
import { authInterceptor } from './app/core/interceptors/auth.interceptor';
import { profileInterceptor } from './app/core/interceptors/profile.interceptor';
import { errorInterceptor } from './app/core/interceptors/error.interceptor';

// Load runtime config first, then restore any existing auth session so guards
// see the user before the first protected route resolves.
function initializeApp(configService: AppConfigService, auth: AuthService) {
  return async () => {
    await configService.load();
    await auth.bootstrap();
  };
}

bootstrapApplication(AppComponent, {
  providers: [
    provideHttpClient(
      withFetch(),
      withInterceptors([authInterceptor, profileInterceptor, errorInterceptor])
    ),
    provideRouter(routes),
    {
      provide: APP_INITIALIZER,
      useFactory: initializeApp,
      deps: [AppConfigService, AuthService],
      multi: true
    },
    // Service worker is only built in production (see angular.json); register it
    // when not in dev mode so the offline shell + local notifications work.
    provideServiceWorker('ngsw-worker.js', {
      enabled: !isDevMode(),
      registrationStrategy: 'registerWhenStable:30000'
    })
  ]
}).catch((err) => console.error(err));
