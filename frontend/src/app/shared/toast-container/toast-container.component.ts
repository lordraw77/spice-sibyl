import { Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { NotificationService, Toast, ToastAction } from '../../core/services/notification.service';
import { TranslatePipe } from '../../core/i18n/translate.pipe';

@Component({
  selector: 'app-toast-container',
  standalone: true,
  imports: [CommonModule, TranslatePipe],
  templateUrl: './toast-container.component.html',
  styleUrl: './toast-container.component.css',
})
export class ToastContainerComponent {
  readonly notifications = inject(NotificationService);

  dismiss(id: string): void {
    this.notifications.dismiss(id);
  }

  click(toast: Toast): void {
    this.notifications.dismiss(toast.id);
    toast.onClick!();
  }

  runAction(toast: Toast, action: ToastAction): void {
    this.notifications.dismiss(toast.id);
    action.onClick();
  }

  trackById(_: number, toast: Toast): string {
    return toast.id;
  }

  icon(type: Toast['type']): string {
    return { error: '✕', warning: '⚠', info: 'ℹ', success: '✓' }[type];
  }
}
