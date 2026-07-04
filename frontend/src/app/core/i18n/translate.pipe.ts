import { Pipe, PipeTransform, inject } from '@angular/core';

import { I18nService } from './i18n.service';

/**
 * `{{ 'key' | t }}` or `{{ 'key' | t: { name: value } }}`.
 *
 * Impure so it re-evaluates when the active locale changes (the app uses Zone
 * change detection, so this runs on the normal CD cycle — the catalog lookup is
 * a cheap object access). Translation itself reads the locale signal, so the
 * pipe's output tracks the current language.
 */
@Pipe({ name: 't', standalone: true, pure: false })
export class TranslatePipe implements PipeTransform {
  private readonly i18n = inject(I18nService);

  transform(key: string, params?: Record<string, string | number>): string {
    return this.i18n.translate(key, params);
  }
}
