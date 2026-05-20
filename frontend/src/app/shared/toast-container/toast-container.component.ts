import { Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { NotificationService, Toast } from '../../core/services/notification.service';

@Component({
  selector: 'app-toast-container',
  standalone: true,
  imports: [CommonModule],
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

  trackById(_: number, toast: Toast): string {
    return toast.id;
  }

  icon(type: Toast['type']): string {
    return { error: '✕', warning: '⚠', info: 'ℹ', success: '✓' }[type];
  }
}
