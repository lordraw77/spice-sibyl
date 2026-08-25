import { Injectable, inject, signal } from '@angular/core';

import { I18nService } from '../../core/i18n/i18n.service';
import { NotificationService } from '../../core/services/notification.service';

/** Images the vision models accept. */
const ACCEPTED = 'image/jpeg,image/png,image/webp,image/gif';

/** Anything larger is rejected before it is read into memory as a data URL. */
const MAX_BYTES = 20 * 1024 * 1024;

/**
 * The image a message can carry (roadmap v2 § 3, P2 — extracted from
 * ChatPageComponent).
 *
 * Three ways in — the file picker, drag and drop, paste — that all ended in
 * the same "validate, read as a data URL, remember the name" sequence written
 * out three times in the page component. One entry point now does the reading;
 * the three handlers only produce a File.
 */
@Injectable()
export class ImageAttachmentService {
  private readonly i18n = inject(I18nService);
  private readonly notifications = inject(NotificationService);

  /** The attached image as a data URL, or null when there is none. */
  readonly dataUrl = signal<string | null>(null);
  /** Its file name, shown as the attachment chip's label. */
  readonly name = signal<string | null>(null);
  /** True while a file is hovering over the drop zone. */
  readonly dragActive = signal(false);

  get attached(): boolean {
    return this.dataUrl() !== null;
  }

  clear(): void {
    this.dataUrl.set(null);
    this.name.set(null);
  }

  /** Open the OS file picker and attach what the user chooses. */
  pick(): void {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = ACCEPTED;
    input.onchange = () => {
      const file = input.files?.[0];
      if (file) this.attach(file, { requireImageType: false });
    };
    input.click();
  }

  onDragOver(event: DragEvent): void {
    event.preventDefault();
    event.stopPropagation();
    this.dragActive.set(true);
  }

  onDragLeave(event: DragEvent): void {
    event.preventDefault();
    event.stopPropagation();
    this.dragActive.set(false);
  }

  onDrop(event: DragEvent): void {
    event.preventDefault();
    event.stopPropagation();
    this.dragActive.set(false);
    const file = event.dataTransfer?.files?.[0];
    if (file) this.attach(file, { requireImageType: true });
  }

  /** Attach the first image on the clipboard, if there is one. */
  onPaste(event: ClipboardEvent): void {
    const items = event.clipboardData?.items;
    if (!items) return;
    for (let i = 0; i < items.length; i++) {
      if (!items[i].type.startsWith('image/')) continue;
      const file = items[i].getAsFile();
      if (!file) continue;
      event.preventDefault();
      this.attach(file, { requireImageType: false, fallbackName: 'pasted-image' });
      break;
    }
  }

  private attach(
    file: File,
    { requireImageType, fallbackName }: { requireImageType: boolean; fallbackName?: string },
  ): void {
    if (requireImageType && !file.type.startsWith('image/')) {
      this.notifications.add(
        'error',
        this.i18n.translate('chat.err.fileTypeTitle'),
        this.i18n.translate('chat.err.fileTypeBody'),
      );
      return;
    }
    if (file.size > MAX_BYTES) {
      this.notifications.add(
        'error',
        this.i18n.translate('chat.err.fileTooBigTitle'),
        this.i18n.translate('chat.err.fileTooBigBody'),
      );
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      this.dataUrl.set(reader.result as string);
      this.name.set(file.name || fallbackName || null);
    };
    reader.readAsDataURL(file);
  }
}
