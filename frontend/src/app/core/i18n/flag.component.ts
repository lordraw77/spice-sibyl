import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';

import { Locale } from './locale';

/**
 * Inline SVG country flags for the language switcher.
 *
 * Regional-indicator flag emoji (🇮🇹 …) don't render on Linux/Windows and many
 * browsers — they fall back to the two-letter code — so we ship self-contained
 * SVGs instead. 4:3 aspect, sized via the `size` input (height in px).
 */
@Component({
  selector: 'app-flag',
  standalone: true,
  imports: [CommonModule],
  template: `
    <svg
      [attr.width]="(size * 4) / 3"
      [attr.height]="size"
      viewBox="0 0 60 45"
      class="flag"
      role="img"
      [attr.aria-label]="code"
      preserveAspectRatio="xMidYMid slice"
    >
      <ng-container [ngSwitch]="code">
        <!-- Italy: green / white / red vertical -->
        <ng-container *ngSwitchCase="'it'">
          <rect width="20" height="45" x="0" fill="#009246" />
          <rect width="20" height="45" x="20" fill="#fff" />
          <rect width="20" height="45" x="40" fill="#ce2b37" />
        </ng-container>
        <!-- France: blue / white / red vertical -->
        <ng-container *ngSwitchCase="'fr'">
          <rect width="20" height="45" x="0" fill="#0055a4" />
          <rect width="20" height="45" x="20" fill="#fff" />
          <rect width="20" height="45" x="40" fill="#ef4135" />
        </ng-container>
        <!-- Germany: black / red / gold horizontal -->
        <ng-container *ngSwitchCase="'de'">
          <rect width="60" height="15" y="0" fill="#000" />
          <rect width="60" height="15" y="15" fill="#dd0000" />
          <rect width="60" height="15" y="30" fill="#ffce00" />
        </ng-container>
        <!-- Spain: red / yellow(2x) / red horizontal -->
        <ng-container *ngSwitchCase="'es'">
          <rect width="60" height="45" fill="#aa151b" />
          <rect width="60" height="22.5" y="11.25" fill="#f1bf00" />
        </ng-container>
        <!-- United Kingdom: Union Jack -->
        <ng-container *ngSwitchDefault>
          <clipPath id="uk-clip"><rect width="60" height="45" /></clipPath>
          <g clip-path="url(#uk-clip)">
            <rect width="60" height="45" fill="#012169" />
            <path d="M0,0 60,45 M60,0 0,45" stroke="#fff" stroke-width="9" />
            <path
              d="M0,0 60,45 M60,0 0,45"
              stroke="#c8102e"
              stroke-width="6"
              clip-path="url(#uk-clip)"
            />
            <path d="M30,0 V45 M0,22.5 H60" stroke="#fff" stroke-width="15" />
            <path d="M30,0 V45 M0,22.5 H60" stroke="#c8102e" stroke-width="9" />
          </g>
        </ng-container>
      </ng-container>
    </svg>
  `,
  styles: [
    `
      .flag {
        display: block;
        border-radius: 2px;
        box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.12) inset;
      }
    `,
  ],
})
export class FlagComponent {
  @Input({ required: true }) code!: Locale;
  @Input() size = 14;
}
