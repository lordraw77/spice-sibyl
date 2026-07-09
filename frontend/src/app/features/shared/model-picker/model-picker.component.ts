import {
  Component,
  EventEmitter,
  Input,
  OnInit,
  Output,
  computed,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { ChatService } from '../../../core/services/chat.service';
import { UserPreferencesService } from '../../../core/services/user-preferences.service';
import { I18nService } from '../../../core/i18n/i18n.service';
import { ChatModel, ProviderSummary } from '../../../core/models/chat.models';

/**
 * Reusable model picker — the same catalog and filters as the chat page's model
 * section (hidden models curated on /providers, provider / capability /
 * availability filters + name search), read from the shared
 * `UserPreferencesService`. Used by the visual-workflow inspector for `model`
 * params so `llm.completion` / `llm.agent` nodes pick a model the same way chat does.
 *
 * Value in/out is the model id (`[value]` + `(valueChange)`), so it drops into any
 * template like a control.
 */
@Component({
  selector: 'app-model-picker',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './model-picker.component.html',
  styleUrls: ['./model-picker.component.css'],
})
export class ModelPickerComponent implements OnInit {
  @Input() value = '';
  @Input() placeholder = 'Select a model…';
  /** When true the list expands inline (pushing content down) instead of a
   *  floating popover — used inside the workflow inspector panel. */
  @Input() inline = false;
  @Output() valueChange = new EventEmitter<string>();

  private readonly chat = inject(ChatService);
  private readonly prefs = inject(UserPreferencesService);
  private readonly i18n = inject(I18nService);

  readonly models = signal<ChatModel[]>([]);
  readonly providers = signal<ProviderSummary[]>([]);
  readonly open = signal(false);

  // Filters — seeded from the same saved preferences the chat page uses.
  readonly search = signal('');
  readonly capability = signal('all');
  readonly availability = signal<'all' | 'free'>('all');
  readonly selectedProviders = signal<string[]>([]);
  readonly hidden = signal<Set<string>>(new Set());

  readonly selectableProviders = computed(() => this.providers().filter((p) => p.enabled));

  readonly availableCapabilities = computed(() => {
    const caps = new Set<string>();
    for (const m of this.models()) for (const c of m.capabilities || []) caps.add(c);
    return ['all', ...Array.from(caps).sort()];
  });

  /** Same filter pipeline as the chat page's `filteredModels`. */
  readonly filtered = computed(() => {
    const cap = this.capability();
    const avail = this.availability();
    const provs = new Set(this.selectedProviders());
    const hidden = this.hidden();
    const q = this.search().trim().toLowerCase();
    return this.models().filter((m) => {
      if (hidden.has(m.id)) return false;
      const providerOk = provs.size === 0 || provs.has(m.provider || '');
      const capabilityOk = cap === 'all' || (m.capabilities || []).includes(cap);
      const availabilityOk = avail === 'all' || !!m.free;
      const searchOk =
        !q || (m.label || m.id).toLowerCase().includes(q) || m.id.toLowerCase().includes(q);
      return providerOk && capabilityOk && availabilityOk && searchOk;
    });
  });

  ngOnInit(): void {
    const p = this.prefs.get();
    this.capability.set(p.capabilityFilter);
    this.availability.set(p.availabilityFilter);
    this.hidden.set(new Set(p.hiddenModels));
    this.chat.models().subscribe({
      next: (res) => {
        this.models.set(res.data ?? []);
        this.providers.set(res.providers ?? []);
        const enabled = new Set((res.providers ?? []).filter((x) => x.enabled).map((x) => x.id));
        // Honour the chat page's saved provider filter, intersected with enabled ones.
        this.selectedProviders.set((p.selectedProviders ?? []).filter((id) => enabled.has(id)));
      },
      error: () => {},
    });
  }

  currentLabel(): string {
    if (!this.value) return this.placeholder;
    const m = this.models().find((x) => x.id === this.value);
    return m ? m.label || m.id : this.value;
  }

  toggle(): void {
    this.open.update((v) => !v);
  }

  pick(id: string): void {
    this.value = id;
    this.valueChange.emit(id);
    this.open.set(false);
  }

  toggleProvider(id: string): void {
    this.selectedProviders.update((list) =>
      list.includes(id) ? list.filter((x) => x !== id) : [...list, id],
    );
  }

  isProviderOn(id: string): boolean {
    return this.selectedProviders().includes(id);
  }

  t(key: string): string {
    return this.i18n.translate(key);
  }
}
