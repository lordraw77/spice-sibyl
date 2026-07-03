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
import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { marked } from 'marked';
import DOMPurify from 'dompurify';

interface HelpDoc {
  slug: string;
  file: string;
  title: string;
}

interface HelpManifest {
  language: string;
  docs: HelpDoc[];
}

/** Single extension point when more languages land in public/docs/. */
const LANG = 'it';

@Component({
  selector: 'app-help-page',
  standalone: true,
  imports: [CommonModule, RouterLink],
  templateUrl: './help-page.component.html',
  styleUrl: './help-page.component.css',
})
export class HelpPageComponent implements OnInit {
  private readonly http = inject(HttpClient);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly sanitizer = inject(DomSanitizer);

  readonly docs = signal<HelpDoc[]>([]);
  readonly activeSlug = signal<string | null>(null);
  readonly content = signal<SafeHtml | null>(null);
  readonly loading = signal(true);
  readonly error = signal(false);

  ngOnInit(): void {
    this.http.get<HelpManifest>(`docs/${LANG}/manifest.json`).subscribe({
      next: (manifest) => {
        this.docs.set(manifest.docs);
        this.route.paramMap.subscribe((params) => {
          const slug = params.get('slug');
          const doc =
            manifest.docs.find((d) => d.slug === slug) ?? manifest.docs[0];
          if (doc) this.loadDoc(doc);
        });
      },
      error: () => {
        this.loading.set(false);
        this.error.set(true);
      },
    });
  }

  private loadDoc(doc: HelpDoc): void {
    this.activeSlug.set(doc.slug);
    this.loading.set(true);
    this.error.set(false);
    this.http
      .get(`docs/${LANG}/${doc.file}`, { responseType: 'text' })
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

    // Images: ../screenshots/x.png → docs/screenshots/x.png (shipped assets).
    el.querySelectorAll('img').forEach((img) => {
      const src = img.getAttribute('src') ?? '';
      const m = src.match(/(?:\.\.\/)?screenshots\/(.+)$/);
      if (m) img.setAttribute('src', `docs/screenshots/${m[1]}`);
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
      // Anything else (../deploy.md, ../features/…, directories): unwrap to text.
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
