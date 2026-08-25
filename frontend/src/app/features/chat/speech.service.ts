import { Injectable, inject, signal } from '@angular/core';

import { I18nService } from '../../core/i18n/i18n.service';

/**
 * Reading assistant replies aloud (roadmap v2 § 3, P2 — extracted from
 * ChatPageComponent).
 *
 * Speech synthesis is entirely a browser concern with its own small state
 * machine — which message is speaking, and the markdown stripping that keeps
 * the voice from reading backticks and asterisks — so it does not belong in a
 * page component that also owns streaming, attachments and conversations.
 */
@Injectable()
export class SpeechService {
  private readonly i18n = inject(I18nService);

  /** Index of the message currently being read, or null when silent. */
  readonly speakingIndex = signal<number | null>(null);

  get supported(): boolean {
    return typeof window !== 'undefined' && 'speechSynthesis' in window;
  }

  /** Read one message aloud in the active UI locale, cancelling any other. */
  speak(content: unknown, index: number): void {
    if (!this.supported) return;
    window.speechSynthesis.cancel();
    const text = this.stripMarkdown(typeof content === 'string' ? content : '');
    if (!text) return;

    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = this.i18n.bcp47();
    utterance.onend = () => this.speakingIndex.set(null);
    utterance.onerror = () => this.speakingIndex.set(null);
    this.speakingIndex.set(index);
    window.speechSynthesis.speak(utterance);
  }

  stop(): void {
    if (!this.supported) return;
    window.speechSynthesis.cancel();
    this.speakingIndex.set(null);
  }

  /** Markdown reads badly out loud: drop the syntax, keep the prose. */
  private stripMarkdown(md: string): string {
    return md
      .replace(/```[\s\S]*?```/g, '')
      .replace(/`[^`]*`/g, '')
      .replace(/!\[.*?\]\(.*?\)/g, '')
      .replace(/\[([^\]]*)\]\(.*?\)/g, '$1')
      .replace(/#{1,6}\s+/g, '')
      .replace(/[*_~]{1,3}/g, '')
      .replace(/>\s+/gm, '')
      .replace(/[-*+]\s+/gm, '')
      .replace(/\d+\.\s+/gm, '')
      .replace(/\n{2,}/g, '. ')
      .replace(/\n/g, ' ')
      .trim();
  }
}
