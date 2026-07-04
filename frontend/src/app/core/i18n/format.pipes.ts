import { Pipe, PipeTransform, inject } from '@angular/core';

import { I18nService } from './i18n.service';

/**
 * Locale-aware formatting pipes (Phase 22.c). All impure so they re-run when
 * the active locale changes — see TranslatePipe for the rationale.
 *
 *   {{ tokens | localeNumber }}
 *   {{ cost | localeCost }}          → USD, symbol placement per locale
 *   {{ epochMs | localeDate }}
 */
@Pipe({ name: 'localeNumber', standalone: true, pure: false })
export class LocaleNumberPipe implements PipeTransform {
  private readonly i18n = inject(I18nService);
  transform(value: number | null | undefined, options?: Intl.NumberFormatOptions): string {
    if (value == null) return '';
    return this.i18n.formatNumber(value, options);
  }
}

@Pipe({ name: 'localeCost', standalone: true, pure: false })
export class LocaleCostPipe implements PipeTransform {
  private readonly i18n = inject(I18nService);
  transform(value: number | null | undefined, fractionDigits = 4): string {
    if (value == null) return '';
    return this.i18n.formatCost(value, fractionDigits);
  }
}

@Pipe({ name: 'localeDate', standalone: true, pure: false })
export class LocaleDatePipe implements PipeTransform {
  private readonly i18n = inject(I18nService);
  transform(
    value: number | Date | null | undefined,
    options?: Intl.DateTimeFormatOptions,
  ): string {
    if (value == null) return '';
    return this.i18n.formatDate(value, options);
  }
}
