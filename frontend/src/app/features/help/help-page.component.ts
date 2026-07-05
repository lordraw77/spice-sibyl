/**
 * HelpPageComponent — in-app guide (/help, /help/:slug).
 *
 * Renders the feature docs copied into public/docs/<lang>/ at build time by
 * frontend/scripts/copy-docs.mjs: a manifest drives the sidebar, the selected
 * markdown file is fetched and rendered client-side.
 *
 * XSS safety: markdown → HTML via marked, sanitized by DOMPurify, then
 * post-processed (image paths, cross-doc links) before being trusted.
 */
import { Component, OnInit, effect, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import { I18nService } from '../../core/i18n/i18n.service';
import { TranslatePipe } from '../../core/i18n/translate.pipe';

interface HelpDoc {
  slug: string;
  file: string;
  title: string;
}

interface HelpManifest {
  language: string;
  docs: HelpDoc[];
}

/** Doc sets published by copy-docs.mjs; the rest fall back to English. */
const PUBLISHED_LANGS = ['it', 'en', 'fr', 'de', 'es'];
const FALLBACK_LANG = 'en';

@Component({
  selector: 'app-help-page',
  standalone: true,
  imports: [CommonModule, RouterLink, TranslatePipe],
  templateUrl: './help-page.component.html',
  styleUrl: './help-page.component.css',
})
export class HelpPageComponent implements OnInit {
  private readonly http = inject(HttpClient);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly sanitizer = inject(DomSanitizer);
  private readonly i18n = inject(I18nService);

  readonly docs = signal<HelpDoc[]>([]);
  readonly activeSlug = signal<string | null>(null);
  readonly content = signal<SafeHtml | null>(null);
  readonly loading = signal(true);
  readonly error = signal(false);

  /** Slug requested by the route, tracked so a locale switch can reload it. */
  private routeSlug: string | null = null;

  constructor() {
    // Reload the manifest + current doc whenever the UI locale changes, so
    // switching language reflects on the Help page without a reload. Runs once
    // on init too (effects fire immediately), which drives the first load.
    effect(() => this.loadManifest(this.docLang()));
  }

  /** Doc language: the active UI locale, or English fallback. Reactive. */
  private docLang(): string {
    const locale = this.i18n.locale();
    return PUBLISHED_LANGS.includes(locale) ? locale : FALLBACK_LANG;
  }

  ngOnInit(): void {
    this.route.paramMap.subscribe((params) => {
      this.routeSlug = params.get('slug');
      this.selectActiveDoc();
    });
  }

  private loadManifest(lang: string): void {
    this.http.get<HelpManifest>(`docs/${lang}/manifest.json`).subscribe({
      next: (manifest) => {
        this.docs.set(manifest.docs);
        this.selectActiveDoc();
      },
      error: () => {
        this.loading.set(false);
        this.error.set(true);
      },
    });
  }

  /** Load the doc for the current route slug (or the active/first one). */
  private selectActiveDoc(): void {
    const docs = this.docs();
    if (!docs.length) return;
    const wanted = this.routeSlug ?? this.activeSlug();
    const doc = docs.find((d) => d.slug === wanted) ?? docs[0];
    if (doc) this.loadDoc(doc);
  }

  private loadDoc(doc: HelpDoc): void {
    this.activeSlug.set(doc.slug);
    this.loading.set(true);
    this.error.set(false);
    this.http
      .get(`docs/${this.docLang()}/${doc.file}`, { responseType: 'text' })
      .subscribe({
        next: (md) => {
          this.content.set(this.render(md));
          this.loading.set(false);
          document.querySelector('.help-content')?.scrollTo?.(0, 0);
          window.scrollTo(0, 0);
        },
        error: () => {
          this.loading.set(false);
          this.error.set(true);
        },
      });
  }

  /** Markdown → sanitized HTML with image/link rewriting for the in-app context. */
  private render(md: string): SafeHtml {
    const html = marked.parse(md, { async: false }) as string;
    const clean = DOMPurify.sanitize(html);

    const el = document.createElement('div');
    el.innerHTML = clean;

    // Images: screenshots/x.png (or legacy ../screenshots/x.png) → the shipped
    // per-language asset docs/<lang>/screenshots/x.png.
    el.querySelectorAll('img').forEach((img) => {
      const src = img.getAttribute('src') ?? '';
      const m = src.match(/(?:\.\.\/)?screenshots\/(.+)$/);
      if (m) img.setAttribute('src', `docs/${this.docLang()}/screenshots/${m[1]}`);
      img.setAttribute('loading', 'lazy');
    });

    const slugs = new Set(this.docs().map((d) => d.slug));
    el.querySelectorAll('a').forEach((a) => {
      const href = a.getAttribute('href') ?? '';
      if (/^https?:/.test(href)) {
        a.setAttribute('target', '_blank');
        a.setAttribute('rel', 'noopener');
        return;
      }
      if (href.startsWith('#')) return; // in-page anchor
      // Sibling doc link (chat.md, README.md#sezione) → in-app route.
      const m = href.match(/^\.?\/?([\w-]+)\.md(#.*)?$/);
      if (m) {
        const slug = m[1] === 'README' ? 'index' : m[1];
        if (slugs.has(slug)) {
          a.setAttribute('href', `/help/${slug}`);
          return;
        }
      }
      // Anything else (../deploy.md, ../en/…, directories): unwrap to text.
      a.replaceWith(...Array.from(a.childNodes));
    });

    return this.sanitizer.bypassSecurityTrustHtml(el.innerHTML);
  }

  /** Links inside [innerHTML] bypass the Angular router; re-route /help/* clicks. */
  onContentClick(ev: MouseEvent): void {
    const a = (ev.target as HTMLElement).closest('a');
    const href = a?.getAttribute('href');
    if (href?.startsWith('/help/')) {
      ev.preventDefault();
      this.router.navigateByUrl(href);
    }
  }
}
