import { Component, EventEmitter, Input, Output } from '@angular/core';
import { CommonModule } from '@angular/common';

import { TranslatePipe } from '../../../core/i18n/translate.pipe';

/** Roadmap fase 1 (1.1) — the editor's edit toolbar (undo/redo, copy/paste,
 *  comment). Pure presentation: the page owns the stacks and the clipboard. */
@Component({
  selector: 'app-editor-toolbar',
  standalone: true,
  imports: [CommonModule, TranslatePipe],
  styles: [':host { display: contents; }'],
  styleUrls: ['../graph-workflow-page.component.css'],
  template: `
    <div class="gwf-toolbar">
      <button class="icon-btn" [disabled]="!canUndo" (click)="undo.emit()" [title]="'gwf.undo' | t">↶</button>
      <button class="icon-btn" [disabled]="!canRedo" (click)="redo.emit()" [title]="'gwf.redo' | t">↷</button>
      <span class="toolbar-sep"></span>
      <button class="icon-btn" [disabled]="!canCopy" (click)="copy.emit()" [title]="'gwf.copyNode' | t">⧉</button>
      <button class="icon-btn" [disabled]="!canPaste" (click)="paste.emit()" [title]="'gwf.pasteNode' | t">📋</button>
      <span class="toolbar-sep"></span>
      <button class="btn small" (click)="addComment.emit()" [title]="'gwf.addComment' | t">
        🗒 {{ 'gwf.addComment' | t }}
      </button>
      <!-- fase 8.2 — sticky note / frame -->
      <button class="btn small" (click)="addNote.emit()" [title]="'gwf.note.addHint' | t">
        📝 {{ 'gwf.note.add' | t }}
      </button>
      <button class="btn small" (click)="addFrame.emit()" [title]="'gwf.note.addFrameHint' | t">
        ▢ {{ 'gwf.note.addFrame' | t }}
      </button>
      <span class="toolbar-sep"></span>
      <button class="btn small" (click)="layout.emit()" [title]="'gwf.autoLayoutHint' | t">
        ⊞ {{ 'gwf.autoLayout' | t }}
      </button>
      <button class="icon-btn" (click)="fit.emit()" [title]="'gwf.fitView' | t">⛶</button>
      <span class="toolbar-sep"></span>
      <!-- fase 8.3 — toggle step-debug mode -->
      <button
        class="btn small"
        [class.active]="debugMode"
        (click)="toggleDebug.emit()"
        [title]="'gwf.debug.toggleHint' | t"
      >🐞 {{ 'gwf.debug.toggle' | t }}</button>
    </div>
  `,
})
export class EditorToolbarComponent {
  @Input() canUndo = false;
  @Input() canRedo = false;
  @Input() canCopy = false;
  @Input() canPaste = false;
  /** Fase 8.3 — step-debug mode is active (button shown pressed). */
  @Input() debugMode = false;

  @Output() undo = new EventEmitter<void>();
  @Output() redo = new EventEmitter<void>();
  @Output() copy = new EventEmitter<void>();
  @Output() paste = new EventEmitter<void>();
  @Output() addComment = new EventEmitter<void>();
  /** Fase 8.2 — add a sticky note / a frame to the canvas. */
  @Output() addNote = new EventEmitter<void>();
  @Output() addFrame = new EventEmitter<void>();
  /** Fase 8.3 — enter/leave step-debug mode. */
  @Output() toggleDebug = new EventEmitter<void>();
  /** Fase 3.5 — layered auto-layout of the graph. */
  @Output() layout = new EventEmitter<void>();
  /** Fase 3.5 — center/fit the graph in the viewport. */
  @Output() fit = new EventEmitter<void>();
}
