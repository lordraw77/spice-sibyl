import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';

import { AppConfigService } from '../../core/config/app-config.service';
import { ProfileService } from '../../core/services/profile.service';
import { NotificationService } from '../../core/services/notification.service';
import { I18nService } from '../../core/i18n/i18n.service';
import { TranslatePipe } from '../../core/i18n/translate.pipe';
import { ReminderPrefsService } from '../../core/services/reminder-prefs.service';
import { TelegramLinkStatus } from '../../core/models/chat.models';
import {
  ReminderCreate,
  ReminderOut,
  RemindersService,
} from '../../core/services/reminders.service';

type RecurrenceKind = 'once' | 'daily' | 'weekly' | 'cron';
type ChannelKind = 'telegram' | 'web' | 'both';

const WEEKDAYS: { key: string; labelKey: string }[] = [
  { key: 'mon', labelKey: 'reminders.day.mon' },
  { key: 'tue', labelKey: 'reminders.day.tue' },
  { key: 'wed', labelKey: 'reminders.day.wed' },
  { key: 'thu', labelKey: 'reminders.day.thu' },
  { key: 'fri', labelKey: 'reminders.day.fri' },
  { key: 'sat', labelKey: 'reminders.day.sat' },
  { key: 'sun', labelKey: 'reminders.day.sun' },
];

/** Phase 23.d — extended reminders: one-shot/recurring/cron, per-channel delivery. */
@Component({
  selector: 'app-reminders-page',
  standalone: true,
  imports: [CommonModule, FormsModule, TranslatePipe],
  templateUrl: './reminders-page.component.html',
  styleUrls: ['./reminders-page.component.css'],
})
export class RemindersPageComponent implements OnInit {
  private readonly remindersSvc = inject(RemindersService);
  private readonly http = inject(HttpClient);
  private readonly config = inject(AppConfigService);
  private readonly profile = inject(ProfileService);
  private readonly notify = inject(NotificationService);
  private readonly i18n = inject(I18nService);
  private readonly reminderPrefs = inject(ReminderPrefsService);

  readonly reminders = this.remindersSvc.reminders;
  readonly loading = signal(false);
  readonly saving = signal(false);
  readonly telegramLink = signal<TelegramLinkStatus>({ linked: false });
  readonly editingId = signal<string | null>(null);
  readonly weekdays = WEEKDAYS;

  // Create/edit form state
  promptMode: 'text' | 'smart' = 'text';
  text = '';
  smartPrompt = '';
  recurrenceKind: RecurrenceKind = 'once';
  selectedWeekdays: string[] = [];
  cronRaw = '0,8,*,*,1-5';
  fireAtLocal = '';
  timezone = '';
  channels: ChannelKind = 'web';

  ngOnInit(): void {
    this.refresh();
    this.loadTelegramLink();
    if (!this.timezone) this.timezone = this.reminderPrefs.timezone() ?? '';
    if (!this.fireAtLocal) this.fireAtLocal = this.defaultFireAtLocal();
  }

  private defaultFireAtLocal(): string {
    return this.toLocalInputValue(Math.floor(Date.now() / 1000) + 5 * 60);
  }

  refresh(): void {
    this.loading.set(true);
    this.remindersSvc.list().subscribe({
      next: () => this.loading.set(false),
      error: () => this.loading.set(false),
    });
  }

  private loadTelegramLink(): void {
    this.http
      .get<TelegramLinkStatus>(`${this.config.apiUrl}/telegram/link/${this.profile.currentId}`)
      .subscribe({
        next: (status) => this.telegramLink.set(status),
        error: () => {},
      });
  }

  get telegramAvailable(): boolean {
    return this.telegramLink().linked;
  }

  toggleWeekday(day: string): void {
    this.selectedWeekdays = this.selectedWeekdays.includes(day)
      ? this.selectedWeekdays.filter((d) => d !== day)
      : [...this.selectedWeekdays, day];
  }

  private buildRecurrence(): string {
    switch (this.recurrenceKind) {
      case 'once':
        return 'once';
      case 'daily':
        return 'daily';
      case 'weekly':
        return `weekly:${this.selectedWeekdays.join(',')}`;
      case 'cron':
        return `cron:${this.cronRaw.trim()}`;
    }
  }

  private parseRecurrence(value: string): void {
    if (value === 'once' || value === 'daily') {
      this.recurrenceKind = value;
      this.selectedWeekdays = [];
      return;
    }
    if (value.startsWith('weekly:')) {
      this.recurrenceKind = 'weekly';
      this.selectedWeekdays = value.slice('weekly:'.length).split(',').filter(Boolean);
      return;
    }
    if (value.startsWith('cron:')) {
      this.recurrenceKind = 'cron';
      this.cronRaw = value.slice('cron:'.length);
      return;
    }
    this.recurrenceKind = 'once';
  }

  get formValid(): boolean {
    const hasPrompt =
      this.promptMode === 'text' ? !!this.text.trim() : !!this.smartPrompt.trim();
    const hasWeekdays = this.recurrenceKind !== 'weekly' || this.selectedWeekdays.length > 0;
    const hasCron = this.recurrenceKind !== 'cron' || !!this.cronRaw.trim();
    return hasPrompt && hasWeekdays && hasCron && !!this.fireAtLocal;
  }

  resetForm(): void {
    this.editingId.set(null);
    this.promptMode = 'text';
    this.text = '';
    this.smartPrompt = '';
    this.recurrenceKind = 'once';
    this.selectedWeekdays = [];
    this.cronRaw = '0,8,*,*,1-5';
    this.fireAtLocal = this.defaultFireAtLocal();
    this.timezone = this.reminderPrefs.timezone() ?? '';
    this.channels = 'web';
  }

  edit(r: ReminderOut): void {
    this.editingId.set(r.id);
    this.promptMode = r.smart_prompt ? 'smart' : 'text';
    this.text = r.text ?? '';
    this.smartPrompt = r.smart_prompt ?? '';
    this.parseRecurrence(r.recurrence);
    this.fireAtLocal = this.toLocalInputValue(r.fire_at);
    this.timezone = r.timezone ?? '';
    this.channels = (r.channels as ChannelKind) ?? 'web';
  }

  private toLocalInputValue(unixSeconds: number): string {
    const d = new Date(unixSeconds * 1000);
    const pad = (n: number) => `${n}`.padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  private fromLocalInputValue(value: string): number {
    return Math.floor(new Date(value).getTime() / 1000);
  }

  save(): void {
    if (!this.formValid) return;
    this.saving.set(true);
    const payload: ReminderCreate = {
      recurrence: this.buildRecurrence(),
      fire_at: this.fromLocalInputValue(this.fireAtLocal),
      channels: this.channels,
      timezone: this.timezone || undefined,
    };
    if (this.promptMode === 'text') payload.text = this.text.trim();
    else payload.smart_prompt = this.smartPrompt.trim();

    const id = this.editingId();
    const req = id ? this.remindersSvc.patch(id, payload) : this.remindersSvc.create(payload);
    req.subscribe({
      next: () => {
        this.saving.set(false);
        this.notify.add('success', this.i18n.translate('reminders.saved'));
        this.resetForm();
      },
      error: () => {
        this.saving.set(false);
        this.notify.add('error', this.i18n.translate('common.error'), this.i18n.translate('reminders.saveFailed'));
      },
    });
  }

  toggleActive(r: ReminderOut): void {
    this.remindersSvc.patch(r.id, { active: !r.active }).subscribe({
      error: () => this.notify.add('error', this.i18n.translate('common.error'), this.i18n.translate('reminders.saveFailed')),
    });
  }

  remove(r: ReminderOut): void {
    if (!window.confirm(this.i18n.translate('reminders.deleteConfirm'))) return;
    this.remindersSvc.remove(r.id).subscribe({
      next: () => {
        if (this.editingId() === r.id) this.resetForm();
      },
      error: () => this.notify.add('error', this.i18n.translate('common.error'), this.i18n.translate('reminders.deleteFailed')),
    });
  }

  snooze(r: ReminderOut): void {
    this.remindersSvc.snooze(r.id, 10).subscribe({
      next: () => this.notify.add('success', this.i18n.translate('reminders.snoozed')),
      error: () => this.notify.add('error', this.i18n.translate('common.error'), this.i18n.translate('reminders.saveFailed')),
    });
  }

  recurrenceLabel(r: ReminderOut): string {
    if (r.recurrence === 'once') return this.i18n.translate('reminders.recurrence.once');
    if (r.recurrence === 'daily') return this.i18n.translate('reminders.recurrence.daily');
    if (r.recurrence.startsWith('weekly:')) {
      const days = r.recurrence.slice('weekly:'.length).split(',').filter(Boolean);
      const labels = days.map((d) => {
        const found = WEEKDAYS.find((w) => w.key === d);
        return found ? this.i18n.translate(found.labelKey) : d;
      });
      return `${this.i18n.translate('reminders.recurrence.weekly')}: ${labels.join(', ')}`;
    }
    if (r.recurrence.startsWith('cron:')) {
      return `${this.i18n.translate('reminders.recurrence.cron')}: ${r.recurrence.slice('cron:'.length)}`;
    }
    return r.recurrence;
  }

  channelLabel(channels: string): string {
    const keys: Record<string, string> = {
      telegram: 'reminders.channel.telegram',
      web: 'reminders.channel.web',
      both: 'reminders.channel.both',
    };
    return keys[channels] ? this.i18n.translate(keys[channels]) : channels;
  }

  fireAtDisplay(r: ReminderOut): string {
    return new Date(r.fire_at * 1000).toLocaleString();
  }
}
