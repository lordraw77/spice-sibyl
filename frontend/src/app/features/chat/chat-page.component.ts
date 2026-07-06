/**
 * ChatPageComponent — main chat interface.
 *
 * Responsibilities:
 *  - Load the model list from the backend and populate the sidebar selectors
 *  - Send user prompts via ChatService and render assistant replies as Markdown
 *  - Display per-message telemetry (latency, token counts, cost)
 *  - Manage auto-scroll: keeps the view pinned to the bottom unless the user
 *    scrolls up, at which point auto-scroll is paused until they return to
 *    within 80 px of the bottom
 *
 * XSS safety: all assistant HTML is parsed by marked then sanitized by
 * DOMPurify before being trusted via Angular's DomSanitizer.  User messages
 * are HTML-escaped and newlines are converted to <br> tags.
 */
import {
  AfterViewChecked,
  Component,
  ElementRef,
  HostListener,
  OnDestroy,
  OnInit,
  ViewChild,
  computed,
  effect,
  inject,
  signal,
} from '@angular/core';
import { Subject, Subscription, debounceTime, distinctUntilChanged, switchMap, of } from 'rxjs';
import { takeUntil } from 'rxjs/operators';
import { CommonModule, DatePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';

import { ChatService } from '../../core/services/chat.service';
import { ChatStateService } from '../../core/services/chat-state.service';
import { ConversationService } from '../../core/services/conversation.service';
import { ProfileService } from '../../core/services/profile.service';
import { TagService } from '../../core/services/tag.service';
import { FeedbackService } from '../../core/services/feedback.service';
import { AuthService } from '../../core/services/auth.service';
import { ProfileModalComponent } from '../profile/profile-modal.component';
import { OnboardingComponent } from '../onboarding/onboarding.component';
import { OnboardingService } from '../../core/services/onboarding.service';
import { ChatCompletionResponse, ChatMessage, ChatModel, ConversationSummary, ProviderSummary, RagSource, SearchResult, Tag, TelegramLinkStatus, ToolDefinition, ToolEvent } from '../../core/models/chat.models';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { marked, Renderer } from 'marked';
import DOMPurify from 'dompurify';
import hljs from 'highlight.js';
import { NotificationService } from '../../core/services/notification.service';
import { AppConfigService } from '../../core/config/app-config.service';
import { UserPreferencesService } from '../../core/services/user-preferences.service';
import { PushNotifyService } from '../../core/services/push-notify.service';
import { NotificationBridgeService } from '../../core/services/notification-bridge.service';
import { I18nService } from '../../core/i18n/i18n.service';
import { TranslatePipe } from '../../core/i18n/translate.pipe';
import { LocaleCostPipe } from '../../core/i18n/format.pipes';

@Component({
  selector: 'app-chat-page',
  standalone: true,
  imports: [CommonModule, FormsModule, DatePipe, RouterLink, ProfileModalComponent, OnboardingComponent, TranslatePipe, LocaleCostPipe],
  templateUrl: './chat-page.component.html',
  styleUrl: './chat-page.component.css',
})
export class ChatPageComponent implements OnInit, AfterViewChecked, OnDestroy {
  private readonly chatService = inject(ChatService);
  private readonly chatState = inject(ChatStateService);
  private readonly conversationService = inject(ConversationService);
  readonly profileService = inject(ProfileService);
  private readonly sanitizer = inject(DomSanitizer);
  private readonly notifications = inject(NotificationService);
  private readonly router = inject(Router);
  private readonly appConfig = inject(AppConfigService);
  private readonly userPrefs = inject(UserPreferencesService);
  private readonly tagService = inject(TagService);
  private readonly feedbackService = inject(FeedbackService);
  private readonly auth = inject(AuthService);
  readonly i18n = inject(I18nService);
  readonly pushNotify = inject(PushNotifyService);
  private readonly notificationBridge = inject(NotificationBridgeService);
  readonly onboarding = inject(OnboardingService);

  /** Build headers for raw fetch() calls, which bypass the auth interceptor. */
  private authHeaders(extra: Record<string, string> = {}): Record<string, string> {
    const headers: Record<string, string> = { ...extra };
    const token = this.auth.token;
    if (token) headers['Authorization'] = `Bearer ${token}`;
    return headers;
  }

  private readonly savedPrefs = this.userPrefs.get();
  readonly availabilityFilter = signal<'all' | 'free'>(this.savedPrefs.availabilityFilter);
  readonly toolsEnabled = signal(this.savedPrefs.toolsEnabled);
  readonly availableTools = signal<ToolDefinition[]>([]);
  readonly modelSearchQuery = signal('');

  /** MCP group collapsed state: key = group name, value = true if expanded */
  readonly mcpGroupExpanded = signal<Record<string, boolean>>({});

  // RAG on/off (management lives in the /knowledge page)
  readonly ragEnabled = signal(this.savedPrefs.ragEnabled);

  // Phase 19: persistent memory — per-chat incognito toggle
  // (memory management lives in the /memory page)
  readonly memoryEnabled = signal(this.savedPrefs.memoryEnabled);

  /** Conversation picker overlay (replaces the old inline Conversations section). */
  readonly convPickerOpen = signal(false);
  readonly modelOpen = signal(this.savedPrefs.sectionsOpen.model);
  readonly systemOpen = signal(this.savedPrefs.sectionsOpen.system);
  readonly paramsOpen = signal(this.savedPrefs.sectionsOpen.params);

  // Tags — the conversation picker uses tag filtering + per-conversation tag
  // assignment; tag CRUD itself lives in the /tags page.
  readonly tags = signal<Tag[]>([]);
  readonly selectedTagFilter = signal<string | null>(null);
  tagAssignConvId: string | null = null;

  // Telegram linking
  telegramLink = signal<TelegramLinkStatus>({ linked: false });
  telegramLinkCode = '';
  telegramLinkLoading = false;

  openConvPicker(): void { this.convPickerOpen.set(true); }
  closeConvPicker(): void { this.convPickerOpen.set(false); this.tagAssignConvId = null; }
  toggleModel(): void { this.modelOpen.update(v => !v); this.userPrefs.setSection('model', this.modelOpen()); }
  toggleSystem(): void { this.systemOpen.update(v => !v); this.userPrefs.setSection('system', this.systemOpen()); }
  toggleParams(): void { this.paramsOpen.update(v => !v); this.userPrefs.setSection('params', this.paramsOpen()); }

  /** Conversations filtered by the selected tag */
  readonly filteredConversations = computed(() => {
    const tag = this.selectedTagFilter();
    const convs = this.conversations();
    if (!tag) return convs;
    return convs.filter(c => c.tags?.some(t => t.id === tag));
  });

  constructor() {
    // Reload the conversation list whenever the active profile changes.
    // Reset messages only when the profile *actually* changes — not on every
    // component re-mount (e.g. the user navigated away and came back).
    // allowSignalWrites is required because newConversation() writes to this.messages.
    effect(() => {
      const profile = this.profileService.current();
      if (profile) {
        this.loadConversationList();
        // Tags stay loaded here: the conversation picker uses tag filter chips
        // and the tag-assign popover. Templates / knowledge / memory are now
        // managed in their own routed pages.
        this.loadTags();
        if (profile.id !== this.chatState.lastActiveProfileId) {
          this.chatState.lastActiveProfileId = profile.id;
          this.currentConversationId = null;
          this.newConversation();
        }
        // First-run guided tour, once a profile exists (modal dismissed) and the
        // chat UI is rendered so [data-tour] targets can be located.
        if (!this.onboardingTriggered) {
          this.onboardingTriggered = true;
          setTimeout(() => this.onboarding.maybeStart(), 500);
        }
      }
    }, { allowSignalWrites: true });
  }

  private onboardingTriggered = false;

  @ViewChild('messagesContainer')
  private messagesContainer?: ElementRef<HTMLDivElement>;

  @ViewChild('searchInput')
  private searchInputEl?: ElementRef<HTMLInputElement>;

  @HostListener('document:keydown', ['$event'])
  onGlobalKeydown(event: KeyboardEvent): void {
    const ctrl = event.ctrlKey || event.metaKey;

    // Ctrl+K → open the conversation picker and focus its search field
    if (ctrl && event.key === 'k') {
      event.preventDefault();
      this.openConvPicker();
      setTimeout(() => this.searchInputEl?.nativeElement?.focus(), 50);
      return;
    }

    // Skip remaining shortcuts when typing in form fields
    const tag = (event.target as HTMLElement)?.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || (event.target as HTMLElement)?.isContentEditable) return;

    // Alt+N → new conversation
    if (event.altKey && event.key === 'n') {
      event.preventDefault();
      this.newConversation();
      return;
    }

    // Ctrl+Shift+S → toggle sidebar
    if (ctrl && event.shiftKey && event.key === 'S') {
      event.preventDefault();
      this.toggleSidebar();
      return;
    }
  }

  // ── Touch swipe to open/close the sidebar on mobile ──────────
  private touchStartX = 0;
  private touchStartY = 0;
  private touchFromLeftEdge = false;

  @HostListener('touchstart', ['$event'])
  onTouchStart(event: TouchEvent): void {
    if (window.innerWidth >= 992 || event.touches.length !== 1) return;
    const t = event.touches[0];
    this.touchStartX = t.clientX;
    this.touchStartY = t.clientY;
    this.touchFromLeftEdge = t.clientX <= 28;
  }

  @HostListener('touchend', ['$event'])
  onTouchEnd(event: TouchEvent): void {
    if (window.innerWidth >= 992 || event.changedTouches.length !== 1) return;
    const t = event.changedTouches[0];
    const dx = t.clientX - this.touchStartX;
    const dy = t.clientY - this.touchStartY;
    // Require a mostly-horizontal swipe to avoid hijacking vertical scrolling.
    if (Math.abs(dx) < 60 || Math.abs(dx) <= Math.abs(dy)) return;

    if (dx > 0 && this.touchFromLeftEdge && !this.sidebarOpen) {
      this.toggleSidebar(); // swipe right from the edge → open
    } else if (dx < 0 && this.sidebarOpen) {
      this.toggleSidebar(); // swipe left → close
    }
  }

  readonly conversations = signal<ConversationSummary[]>([]);
  readonly searchResults = signal<SearchResult[]>([]);
  readonly searchQuery = signal('');
  readonly isSearching = signal(false);
  private readonly searchSubject = new Subject<string>();
  private readonly destroy$ = new Subject<void>();
  private streamSubscription: Subscription | null = null;

  /** Show profile selector modal when no profile is active */
  readonly showProfileModal = computed(() => !this.profileService.current());

  // Alias to the singleton service signal so state survives navigation.
  readonly messages = this.chatState.messages;
  readonly models = signal<ChatModel[]>([]);
  /** Providers exposed by the backend; used by the Model-section provider filter. */
  readonly providers = signal<ProviderSummary[]>([]);
  readonly capabilityFilter = signal<string>(this.savedPrefs.capabilityFilter);
  /** Personal per-chat filter: which providers' models appear in the model picker. */
  readonly selectedProviders = signal<string[]>([]);

  /** Enabled providers only — the ones offered in the Model-section filter. */
  readonly selectableProviders = computed(() => this.providers().filter((p) => p.enabled));

  /** Models hidden from the picker (curated on the /providers page). */
  readonly hiddenModels = signal<Set<string>>(new Set(this.savedPrefs.hiddenModels));

  /** Sorted list of all capabilities present across loaded models, prefixed with 'all'. */
  readonly availableCapabilities = computed(() => {
    const caps = new Set<string>();
    for (const model of this.models()) {
      for (const capability of model.capabilities || []) {
        caps.add(capability);
      }
    }
    return ['all', ...Array.from(caps).sort()];
  });

  /** Models visible in the selector after applying provider, capability, availability, and name search filters. */
  readonly filteredModels = computed(() => {
    const capability = this.capabilityFilter();
    const availability = this.availabilityFilter();
    const enabledProviders = new Set(this.selectedProviders());
    const hidden = this.hiddenModels();
    const search = this.modelSearchQuery().trim().toLowerCase();

    return this.models().filter((model) => {
      if (hidden.has(model.id)) return false;

      const providerOk =
        enabledProviders.size === 0 || enabledProviders.has(model.provider || '');

      const capabilityOk =
        capability === 'all' || (model.capabilities || []).includes(capability);

      const availabilityOk =
        availability === 'all' || !!model.free;

      const searchOk =
        !search ||
        (model.label || model.id).toLowerCase().includes(search) ||
        (model.id).toLowerCase().includes(search);

      return providerOk && capabilityOk && availabilityOk && searchOk;
    });
  });

  /** Tools grouped by MCP server name. Built-in tools use the group key
   *  '__builtin__'; user-defined custom tools (custom__*) the group 'custom'. */
  readonly mcpToolGroups = computed(() => {
    const groups = new Map<string, ToolDefinition[]>();
    for (const tool of this.availableTools()) {
      const name = tool.function.name;
      const mcpMatch = name.match(/^mcp__(.+?)__/);
      const groupKey = mcpMatch ? mcpMatch[1] : name.startsWith('custom__') ? 'custom' : '__builtin__';
      const list = groups.get(groupKey) ?? [];
      list.push(tool);
      groups.set(groupKey, list);
    }
    return Array.from(groups.entries()).map(([name, tools]) => ({ name, tools }));
  });

  toggleMcpGroup(name: string): void {
    this.mcpGroupExpanded.update(state => ({ ...state, [name]: !state[name] }));
  }

  isMcpGroupExpanded(name: string): boolean {
    return !!this.mcpGroupExpanded()[name];
  }

  prompt = '';
  model = this.savedPrefs.selectedModel ?? 'ollama/qwen2.5:7b-instruct';
  systemPrompt = '';
  temperature = this.savedPrefs.temperature;
  maxTokens = this.savedPrefs.maxTokens;
  maxToolIterations = this.savedPrefs.maxToolIterations;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  voiceRecognition: any = null;
  isListening = false;
  copiedMessageIdx: number | null = null;
  sidebarOpen = this.savedPrefs.sidebarOpen;

  /** Base64-encoded image attached by the user for vision input */
  attachedImageB64: string | null = null;
  attachedImageName: string | null = null;
  /** True while a file is being dragged over the chat area */
  dragActive = false;

  /** Branch navigation: tracks the active branch index per parent message ID */
  readonly activeBranches = signal<Record<string, number>>({});
  /** True while an /imagine request is in-flight */
  generatingImage = false;

  /** Pinned messages for the current conversation */
  readonly pinnedMessages = signal<ChatMessage[]>([]);

  /** Index of the message currently being spoken, or null */
  speakingMessageIdx: number | null = null;

  /** Slash command autocomplete */
  readonly slashCommands: { cmd: string; desc: string; insert: string }[] = [
    { cmd: '/imagine', desc: 'chat.slash.imagine', insert: '/imagine ' },
    { cmd: '/new', desc: 'chat.slash.new', insert: '/new' },
    { cmd: '/model', desc: 'chat.slash.model', insert: '/model ' },
    { cmd: '/export md', desc: 'chat.slash.exportMd', insert: '/export md' },
    { cmd: '/export json', desc: 'chat.slash.exportJson', insert: '/export json' },
  ];
  showSlashMenu = false;
  filteredSlashCommands: { cmd: string; desc: string; insert: string }[] = [];
  slashMenuIndex = 0;

  // Delegated to ChatStateService so state survives navigation away and back.
  get loading(): boolean { return this.chatState.loading(); }
  set loading(v: boolean) { this.chatState.loading.set(v); }

  get streaming(): boolean { return this.chatState.streaming(); }
  set streaming(v: boolean) { this.chatState.streaming.set(v); }

  get currentConversationId(): string | null { return this.chatState.currentConversationId; }
  set currentConversationId(v: string | null) { this.chatState.currentConversationId = v; }

  toggleSidebar(): void {
    this.sidebarOpen = !this.sidebarOpen;
    this.userPrefs.set('sidebarOpen', this.sidebarOpen);
  }
  /** When true, the view scrolls to the bottom on the next AfterViewChecked cycle. */
  private shouldAutoScroll = true;

  toggleTools(): void {
    this.toolsEnabled.update(v => !v);
    this.userPrefs.set('toolsEnabled', this.toolsEnabled());
  }

  toggleRag(): void {
    this.ragEnabled.update(v => !v);
    this.userPrefs.set('ragEnabled', this.ragEnabled());
  }

  onModelChange(modelId: string): void {
    this.model = modelId;
    this.userPrefs.set('selectedModel', modelId);
  }

  onTemperatureChange(value: number): void {
    this.temperature = value;
    this.userPrefs.set('temperature', value);
  }

  onMaxTokensChange(value: number): void {
    this.maxTokens = value;
    this.userPrefs.set('maxTokens', value);
  }

  onMaxToolIterationsChange(value: number): void {
    this.maxToolIterations = value;
    this.userPrefs.set('maxToolIterations', value);
  }

  toolArgsSummary(args: Record<string, unknown> | undefined): string {
    if (!args || !Object.keys(args).length) return '';
    return Object.entries(args)
      .map(([k, v]) => `${k}: ${JSON.stringify(v)}`)
      .join(', ');
  }

  isToolCallPending(events: ToolEvent[] | undefined, callId: string): boolean {
    if (!events) return false;
    return !events.some(e => e.kind === 'result' && e.id === callId);
  }

  ngOnDestroy(): void {
    this.destroy$.next();
    this.destroy$.complete();
  }

  onSearchInput(value: string): void {
    this.searchQuery.set(value);
    this.searchSubject.next(value.trim());
  }

  clearSearch(): void {
    this.searchQuery.set('');
    this.searchResults.set([]);
    this.isSearching.set(false);
  }

  selectSearchResult(result: SearchResult): void {
    this.clearSearch();
    this.selectConversation(result.id);
  }

  ngOnInit(): void {
    this.systemPrompt = this.userPrefs.get().systemPrompt ?? '';

    const renderer = new Renderer();
    // marked v12 still uses old positional signature: (code, lang, escaped)
    (renderer as unknown as Record<string, unknown>)['code'] =
      (code: string, lang: string | undefined): string => {
        const language = lang && hljs.getLanguage(lang) ? lang : 'plaintext';
        const highlighted = hljs.highlight(code, { language }).value;
        return `<pre><code class="hljs language-${language}">${highlighted}</code></pre>`;
      };
    marked.use({ renderer, breaks: true, gfm: true });

    this.chatService.listTools().subscribe({
      next: tools => this.availableTools.set(tools),
      error: () => {},
    });

    this.loadTags();
    this.loadTelegramLink();

    this.searchSubject.pipe(
      debounceTime(300),
      distinctUntilChanged(),
      switchMap(q => {
        if (!q) { this.searchResults.set([]); this.isSearching.set(false); return of([]); }
        this.isSearching.set(true);
        return this.conversationService.search(q, this.profileService.currentId);
      }),
      takeUntil(this.destroy$),
    ).subscribe({
      next: results => { this.searchResults.set(results); this.isSearching.set(false); },
      error: () => { this.searchResults.set([]); this.isSearching.set(false); },
    });

    this.loadConversationList();

    this.chatService.models().subscribe({
      next: (response) => {
        this.models.set(response.data);
        this.providers.set(response.providers || []);

        const savedProviders = this.savedPrefs.selectedProviders;
        const enabledProviderIds = new Set(
          (response.providers || []).filter(p => p.enabled).map(p => p.id)
        );
        if (savedProviders.length && savedProviders.some(id => enabledProviderIds.has(id))) {
          this.selectedProviders.set(savedProviders.filter(id => enabledProviderIds.has(id)));
        } else {
          this.selectedProviders.set(Array.from(enabledProviderIds));
        }

        const savedModel = this.savedPrefs.selectedModel;
        const preferred = response.data.find(item => item.id === (savedModel ?? this.model));
        if (preferred) {
          this.model = preferred.id;
        } else if (response.data.length > 0) {
          this.model = response.data.find(item => item.default)?.id || response.data[0].id;
        }

        this.ensureValidSelectedModel();
        this.queueScrollToBottom(true);
      },
      error: () => {
        this.messages.update((items) => [
          ...items,
          {
            role: 'assistant',
            content: this.i18n.translate('chat.err.loadModels'),
            model: 'system',
            created_at: Math.floor(Date.now() / 1000),
          },
        ]);
        this.queueScrollToBottom(true);
      },
    });
  }

  ngAfterViewChecked(): void {
    if (this.shouldAutoScroll) {
      this.scrollToBottom();
      this.shouldAutoScroll = false;
    }
  }

  onEnter(event: KeyboardEvent): void {
    if (this.showSlashMenu) {
      event.preventDefault();
      this.selectSlashCommand(this.filteredSlashCommands[this.slashMenuIndex]);
      return;
    }
    if (!event.shiftKey) {
      event.preventDefault();
      this.send();
    }
  }

  onPromptInput(): void {
    if (this.prompt.startsWith('/')) {
      const query = this.prompt.toLowerCase();
      this.filteredSlashCommands = this.slashCommands.filter(c => c.cmd.startsWith(query) || c.insert.toLowerCase().startsWith(query));
      this.showSlashMenu = this.filteredSlashCommands.length > 0;
      this.slashMenuIndex = 0;
    } else {
      this.showSlashMenu = false;
    }
  }

  onComposerKeydown(event: KeyboardEvent): void {
    if (!this.showSlashMenu) return;
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      this.slashMenuIndex = (this.slashMenuIndex + 1) % this.filteredSlashCommands.length;
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      this.slashMenuIndex = (this.slashMenuIndex - 1 + this.filteredSlashCommands.length) % this.filteredSlashCommands.length;
    } else if (event.key === 'Tab') {
      event.preventDefault();
      this.selectSlashCommand(this.filteredSlashCommands[this.slashMenuIndex]);
    } else if (event.key === 'Escape') {
      this.showSlashMenu = false;
    }
  }

  selectSlashCommand(cmd: { cmd: string; desc: string; insert: string }): void {
    if (!cmd) return;
    this.prompt = cmd.insert;
    this.showSlashMenu = false;
    // Execute immediate commands
    if (cmd.cmd === '/new') {
      this.prompt = '';
      this.newConversation();
    } else if (cmd.cmd === '/export md') {
      this.prompt = '';
      this.exportConversation('md');
    } else if (cmd.cmd === '/export json') {
      this.prompt = '';
      this.exportConversation('json');
    }
  }

  send(overrideMessages?: ChatMessage[]): void {
    this.showSlashMenu = false;
    const text = this.prompt.trim();
    if (!overrideMessages && (!text && !this.attachedImageB64 || this.loading)) {
      return;
    }

    // /imagine command → text-to-image generation
    if (!overrideMessages && text.startsWith('/imagine ')) {
      this.handleImagineCommand(text.slice(9).trim());
      return;
    }

    this.loading = true;
    const attachedImage = this.attachedImageB64;
    if (!overrideMessages) {
      this.prompt = '';
      this.attachedImageB64 = null;
      this.attachedImageName = null;
    }

    const userMsg: ChatMessage = {
      role: 'user' as const,
      content: text || (attachedImage ? this.i18n.translate('chat.vision.describe') : ''),
      created_at: Math.floor(Date.now() / 1000),
      image_b64: attachedImage ?? undefined,
    };

    const baseMessages = overrideMessages ?? [
      ...this.messages(),
      userMsg,
    ];

    const systemMsg = this.systemPrompt.trim();
    const userMessages = systemMsg
      ? [{ role: 'system' as const, content: systemMsg }, ...baseMessages.filter(m => m.role !== 'system')]
      : baseMessages.filter(m => m.role !== 'system');

    if (!overrideMessages) {
      // Add user message + empty assistant placeholder for streaming
      this.messages.set([
        ...baseMessages,
        { role: 'assistant' as const, content: '', model: this.model, created_at: Math.floor(Date.now() / 1000) },
      ]);
    } else {
      this.messages.update(items => [
        ...items,
        { role: 'assistant' as const, content: '', model: this.model, created_at: Math.floor(Date.now() / 1000) },
      ]);
    }
    this.streaming = true;
    this.queueScrollToBottom();

    // Timestamp used to decide whether the generation was "long-running" and
    // therefore worth a background notification on completion.
    const streamStartedAt = Date.now();
    const targetConversationId = this.currentConversationId;

    // Index of the assistant placeholder in the messages array
    const streamingIdx = this.messages().length - 1;

    const tools = this.toolsEnabled() && this.availableTools().length
      ? this.availableTools()
      : undefined;

    const temperature = this.temperature;
    const maxTokens = this.maxTokens > 0 ? this.maxTokens : undefined;

    const apiMessages = userMessages.map(m => {
      const { tool_events, image_b64, image_url, ...rest } = m as ChatMessage & Record<string, unknown>;
      if (image_b64 && typeof image_b64 === 'string') {
        const parts: Record<string, unknown>[] = [];
        if (rest.content) {
          parts.push({ type: 'text', text: rest.content });
        }
        parts.push({ type: 'image_url', image_url: { url: image_b64 } });
        return { ...rest, content: parts };
      }
      return rest;
    });

    this.streamSubscription = this.chatService.stream({
      model: this.model,
      messages: apiMessages as ChatMessage[],
      stream: true,
      temperature,
      max_tokens: maxTokens,
      tools,
      max_tool_iterations: tools && this.maxToolIterations > 0 ? this.maxToolIterations : undefined,
      rag: this.ragEnabled() || undefined,
      memory: this.memoryEnabled() ? undefined : false,
      profile_id: this.profileService.currentId,
    }).subscribe({
      next: ({ event, data }) => {
        if (event === 'done') {
          return;
        }

        // Phase 19: this reply is grounded with the profile's persistent memory
        if (event === 'memory_context') {
          this.messages.update(items =>
            items.map((m, i) => (i === streamingIdx ? { ...m, memory_used: true } : m))
          );
          return;
        }

        // RAG context event — knowledge-base chunks used to ground the reply
        if (event === 'rag_context') {
          const sources = (data['sources'] as RagSource[] | undefined) ?? [];
          if (sources.length) {
            this.messages.update(items =>
              items.map((m, i) => (i === streamingIdx ? { ...m, rag_sources: sources } : m))
            );
          }
          return;
        }

        // Provider fallback event — the requested provider failed before output,
        // so the gateway switched to the next CHAT_FALLBACK_CHAIN entry.
        if (event === 'provider_switch') {
          const from = data['from'] as string;
          const to = data['to'] as string;
          this.messages.update(items =>
            items.map((m, i) => (i === streamingIdx ? { ...m, provider_switch: { from, to } } : m))
          );
          return;
        }

        // Tool call event — add to the assistant placeholder's tool_events
        if (event === 'tool_call') {
          const toolEvent: ToolEvent = {
            kind: 'call',
            id: data['id'] as string,
            name: data['name'] as string,
            arguments: data['arguments'] as Record<string, unknown>,
          };
          this.messages.update(items =>
            items.map((m, i) =>
              i === streamingIdx
                ? { ...m, tool_events: [...(m.tool_events ?? []), toolEvent] }
                : m
            )
          );
          return;
        }

        // Tool result event — append to tool_events
        if (event === 'tool_result') {
          const toolEvent: ToolEvent = {
            kind: 'result',
            id: data['id'] as string,
            name: data['name'] as string,
            result: data['result'] as string,
          };
          this.messages.update(items =>
            items.map((m, i) =>
              i === streamingIdx
                ? { ...m, tool_events: [...(m.tool_events ?? []), toolEvent] }
                : m
            )
          );
          return;
        }

        // Final meta chunk carries full telemetry — update the placeholder in place
        if (data['object'] === 'chat.completion.meta') {
          const choice = (data['choices'] as { message: Record<string, unknown> }[] | undefined)?.[0];
          const metaMsg = choice?.message;
          const metrics = data['metrics'] as Record<string, unknown> | undefined;
          const usage = data['usage'] as Record<string, unknown> | undefined;

          if (metaMsg) {
            this.messages.update(items =>
              items.map((m, i) =>
                i === streamingIdx
                  ? {
                      ...m,
                      provider: (metaMsg['provider'] ?? metrics?.['provider']) as string | undefined,
                      latency_ms: (metaMsg['latency_ms'] ?? metrics?.['latency_ms']) as number | undefined,
                      first_token_ms: (metaMsg['first_token_ms'] ?? metrics?.['first_token_ms']) as number | undefined,
                      prompt_tokens: (metaMsg['prompt_tokens'] ?? usage?.['prompt_tokens']) as number | undefined,
                      completion_tokens: (metaMsg['completion_tokens'] ?? usage?.['completion_tokens']) as number | undefined,
                      total_tokens: (metaMsg['total_tokens'] ?? usage?.['total_tokens']) as number | undefined,
                      tokens_per_second: (metaMsg['tokens_per_second'] ?? metrics?.['tokens_per_second']) as number | undefined,
                      finish_reason: metaMsg['finish_reason'] as string | undefined,
                      estimated_cost: metrics?.['estimated_cost'] as number | undefined,
                      created_at: (metaMsg['created_at'] ?? data['created']) as number | undefined,
                      capabilities: (metaMsg['capabilities'] as string[] | undefined) ?? [],
                      free: metaMsg['free'] as boolean | undefined,
                      cached: (data['cached'] ?? metaMsg['cached']) as boolean | undefined,
                    }
                  : m
              )
            );
          }
          return;
        }

        // Regular streaming chunk — append delta content to the placeholder
        const delta = (data['choices'] as { delta?: { content?: string } }[] | undefined)?.[0]?.delta?.content;
        if (delta) {
          this.messages.update(items =>
            items.map((m, i) => i === streamingIdx ? { ...m, content: m.content + delta } : m)
          );
          this.queueScrollToBottom();
        }
      },
      error: (err: Error) => {
        const detail = err?.message || this.i18n.translate('chat.err.requestBody');
        this.notifications.add('error', this.i18n.translate('chat.err.requestTitle'), detail);
        this.messages.update(items =>
          items.map((m, i) =>
            i === streamingIdx && !m.content
              ? { ...m, content: `⚠ ${detail}` }
              : m
          )
        );
        this.loading = false;
        this.streaming = false;
        this.queueScrollToBottom();
      },
      complete: () => {
        this.streamSubscription = null;
        this.loading = false;
        this.streaming = false;
        this.queueScrollToBottom();
        const lastUserMsg = baseMessages.filter(m => m.role === 'user').pop();
        if (lastUserMsg) {
          this.persistExchange(lastUserMsg, streamingIdx);
        }
        if (!this.router.url.startsWith('/chat')) {
          this.notifications.add('success', this.i18n.translate('chat.notify.replyTitle'), this.i18n.translate('chat.notify.replyBody'), 6000, () => this.router.navigate(['/chat']));
        }
        // Background system notification for long-running generations (>10s)
        // when the tab is hidden — see PushNotifyService for the guards.
        if (Date.now() - streamStartedAt > 10_000) {
          const reply = this.messages()[streamingIdx]?.content ?? '';
          const preview = reply.replace(/\s+/g, ' ').trim().slice(0, 120) || this.i18n.translate('chat.notify.replyReady');
          const pushTitle = this.i18n.translate('chat.notify.pushTitle');
          this.pushNotify.notifyComplete(pushTitle, preview, () => {
            this.router.navigate(['/chat']).then(() => {
              if (targetConversationId && targetConversationId !== this.currentConversationId) {
                this.selectConversation(targetConversationId);
              }
            });
          });
          // Phase 23.c: the tab-hidden condition is only observable client-side —
          // forward it to the backend so linked users also get a Telegram push.
          if (document.hidden) {
            void this.notificationBridge.trigger('longCompletionDone', pushTitle, preview);
          }
        }
      },
    });
  }

  // ── Persistent memory (Phase 19) ───────────────────────────
  /** Per-chat incognito toggle: OFF = no injection/extraction for new exchanges. */
  toggleMemory(): void {
    this.memoryEnabled.update(v => !v);
    this.userPrefs.set('memoryEnabled', this.memoryEnabled());
  }

  // ── Feedback 👍/👎 (Phase 19) ──────────────────────────────
  rateMessage(message: ChatMessage, idx: number, rating: 1 | -1): void {
    if (!message.id) return;
    // Clicking the active rating clears it.
    if (message.rating === rating) {
      this.feedbackService.clear(message.id).subscribe({
        next: () => this.messages.update(items =>
          items.map((m, i) => i === idx ? { ...m, rating: null, feedback_note: null } : m)),
        error: () => {},
      });
      return;
    }
    let note: string | undefined;
    if (rating === -1) {
      note = prompt(this.i18n.translate('chat.feedback.prompt')) || undefined;
    }
    this.feedbackService.rate(message.id, rating, note).subscribe({
      next: () => this.messages.update(items =>
        items.map((m, i) => i === idx ? { ...m, rating, feedback_note: note ?? null } : m)),
      error: () => this.notifications.add('error', 'Feedback', this.i18n.translate('chat.feedback.sendFailed')),
    });
  }

  // ── Tags ──────────────────────────────────────────────────
  loadTags(): void {
    this.tagService.list(this.profileService.currentId).subscribe({
      next: list => this.tags.set(list),
      error: () => {},
    });
  }

  setTagFilter(tagId: string | null): void {
    this.selectedTagFilter.set(tagId);
  }

  tagPopoverStyle: Record<string, string> = {};

  openTagAssign(convId: string, event: Event): void {
    event.stopPropagation();
    if (this.tagAssignConvId === convId) {
      this.tagAssignConvId = null;
      return;
    }
    this.tagAssignConvId = convId;
    const el = event.target as HTMLElement;
    const rect = el.closest('.conversation-item')?.getBoundingClientRect();
    if (rect) {
      this.tagPopoverStyle = {
        top: rect.bottom + 4 + 'px',
        left: rect.left + 'px',
        width: rect.width + 'px',
      };
    }
  }

  toggleConversationTag(convId: string, tagId: string): void {
    const conv = this.conversations().find(c => c.id === convId);
    const currentTags = conv?.tags?.map(t => t.id) ?? [];
    const newTags = currentTags.includes(tagId)
      ? currentTags.filter(id => id !== tagId)
      : [...currentTags, tagId];
    this.tagService.setConversationTags(convId, newTags).subscribe({
      next: () => this.loadConversationList(),
      error: () => this.notifications.add('error', this.i18n.translate('common.error'), this.i18n.translate('chat.err.updateTags')),
    });
  }

  convHasTag(convId: string, tagId: string): boolean {
    return this.conversations().find(c => c.id === convId)?.tags?.some(t => t.id === tagId) ?? false;
  }

  // ── Telegram Linking ───────────────────────────────────────
  loadTelegramLink(): void {
    const profileId = this.profileService.currentId;
    fetch(`${this.appConfig.apiUrl}/telegram/link/${profileId}`, { headers: this.authHeaders() })
      .then(r => r.json())
      .then(data => this.telegramLink.set(data))
      .catch(() => {});
  }

  submitTelegramLink(): void {
    const code = this.telegramLinkCode.trim();
    if (!code) return;
    this.telegramLinkLoading = true;
    fetch(`${this.appConfig.apiUrl}/telegram/link`, {
      method: 'POST',
      headers: this.authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ code, profile_id: this.profileService.currentId }),
    })
      .then(r => { if (!r.ok) throw new Error(); return r.json(); })
      .then(data => {
        this.telegramLink.set(data);
        this.telegramLinkCode = '';
        this.telegramLinkLoading = false;
        this.notifications.add('success', this.i18n.translate('chat.telegram.linkedTitle'), this.i18n.translate('chat.telegram.linkedBody', { user: data.username || this.i18n.translate('chat.telegram.unknown') }));
      })
      .catch(() => {
        this.telegramLinkLoading = false;
        this.notifications.add('error', this.i18n.translate('common.error'), this.i18n.translate('chat.telegram.invalidCode'));
      });
  }

  unlinkTelegram(): void {
    fetch(`${this.appConfig.apiUrl}/telegram/link/${this.profileService.currentId}`, { method: 'DELETE', headers: this.authHeaders() })
      .then(() => {
        this.telegramLink.set({ linked: false });
        this.notifications.add('success', this.i18n.translate('chat.telegram.unlinkedTitle'), this.i18n.translate('chat.telegram.unlinkedBody'));
      })
      .catch(() => this.notifications.add('error', this.i18n.translate('common.error'), this.i18n.translate('chat.telegram.unlinkFailed')));
  }

  // ── TTS (Text-to-Speech) ───────────────────────────────────
  get hasSpeechSynthesis(): boolean {
    return typeof window !== 'undefined' && 'speechSynthesis' in window;
  }

  speakMessage(message: ChatMessage, idx: number): void {
    if (!this.hasSpeechSynthesis) return;
    window.speechSynthesis.cancel();
    const text = this.stripMarkdown(typeof message.content === 'string' ? message.content : '');
    if (!text) return;
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = this.i18n.bcp47();
    utterance.onend = () => { this.speakingMessageIdx = null; };
    utterance.onerror = () => { this.speakingMessageIdx = null; };
    this.speakingMessageIdx = idx;
    window.speechSynthesis.speak(utterance);
  }

  stopSpeaking(): void {
    window.speechSynthesis.cancel();
    this.speakingMessageIdx = null;
  }

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

  // ── Message Pins ───────────────────────────────────────────
  togglePin(message: ChatMessage, idx: number): void {
    if (!this.currentConversationId || !message.id) return;
    this.conversationService.togglePin(this.currentConversationId, message.id).subscribe({
      next: ({ pinned }) => {
        this.messages.update(items =>
          items.map((m, i) => i === idx ? { ...m, pinned } : m)
        );
        this.loadPinnedMessages();
      },
      error: () => this.notifications.add('error', this.i18n.translate('common.error'), this.i18n.translate('chat.err.updatePin')),
    });
  }

  loadPinnedMessages(): void {
    if (!this.currentConversationId) {
      this.pinnedMessages.set([]);
      return;
    }
    this.conversationService.getPins(this.currentConversationId).subscribe({
      next: pins => this.pinnedMessages.set(pins),
      error: () => {},
    });
  }

  scrollToMessage(messageId: string): void {
    const msgs = this.messages();
    const idx = msgs.findIndex(m => m.id === messageId);
    if (idx === -1) return;
    const container = this.messagesContainer?.nativeElement;
    if (!container) return;
    const messageEls = container.querySelectorAll('.message');
    if (messageEls[idx]) {
      messageEls[idx].scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }

  pinPreview(message: ChatMessage): string {
    const content = typeof message.content === 'string' ? message.content : '';
    return content.slice(0, 40) + (content.length > 40 ? '…' : '');
  }

  saveSystemPrompt(): void {
    this.userPrefs.set('systemPrompt', this.systemPrompt);
    this.notifications.add('success', this.i18n.translate('chat.system.savedTitle'), this.i18n.translate('chat.system.savedBody'));
  }

  clearSystemPrompt(): void {
    this.systemPrompt = '';
    this.userPrefs.set('systemPrompt', '');
  }

  copyMessage(message: ChatMessage, idx: number): void {
    const text = typeof message.content === 'string' ? message.content : JSON.stringify(message.content);
    navigator.clipboard.writeText(text).then(() => {
      this.copiedMessageIdx = idx;
      setTimeout(() => { this.copiedMessageIdx = null; }, 1800);
    }).catch(() => {
      this.notifications.add('error', this.i18n.translate('chat.err.copyTitle'), this.i18n.translate('chat.err.copyBody'));
    });
  }

  regenerate(): void {
    if (this.loading) return;
    const msgs = this.messages();
    const lastAssistant = msgs.length - 1;
    if (lastAssistant < 0 || msgs[lastAssistant].role !== 'assistant') return;

    const assistantMsg = msgs[lastAssistant];
    const parentId = assistantMsg.parent_id;

    // Find the user message to re-send
    let userMsgIdx = -1;
    for (let i = lastAssistant - 1; i >= 0; i--) {
      if (msgs[i].role === 'user') { userMsgIdx = i; break; }
    }
    if (userMsgIdx === -1) return;

    if (parentId && this.currentConversationId) {
      // Branching mode: keep old response, create a new branch
      const historyUpToUser = msgs.slice(0, userMsgIdx + 1);
      const newBranchIndex = (assistantMsg.branch_index ?? 0) + 1;

      // Update branch_count on the current message
      this.messages.update(items =>
        items.map((m, i) => i === lastAssistant
          ? { ...m, branch_count: (m.branch_count ?? 1) + 1 }
          : m
        )
      );

      // Store the parent_id for the new response
      this._pendingBranchParentId = parentId;
      this._pendingBranchIndex = newBranchIndex;
      this.send(historyUpToUser);
    } else {
      // No branching yet — simple regenerate (replace)
      const historyWithoutLastAssistant = msgs.slice(0, userMsgIdx + 1);
      this.messages.set(historyWithoutLastAssistant);
      this.send(historyWithoutLastAssistant);
    }
  }

  private _pendingBranchParentId: string | null = null;
  private _pendingBranchIndex = 0;

  switchBranch(message: ChatMessage, direction: -1 | 1): void {
    if (!message.parent_id || !this.currentConversationId) return;
    const currentIdx = message.branch_index ?? 0;
    const newIdx = currentIdx + direction;
    if (newIdx < 0 || newIdx >= (message.branch_count ?? 1)) return;

    this.conversationService.getBranches(this.currentConversationId, message.parent_id).subscribe({
      next: (siblings) => {
        const target = siblings.find(s => s.branch_index === newIdx);
        if (!target) return;
        target.branch_count = siblings.length;
        this.messages.update(items =>
          items.map((m) =>
            m.parent_id === message.parent_id && m.role === 'assistant'
              ? target
              : m
          )
        );
        this.activeBranches.update(b => ({ ...b, [message.parent_id!]: newIdx }));
      },
    });
  }

  editLastUserMessage(): void {
    if (this.loading) return;
    const msgs = this.messages();
    const lastUserIdx = [...msgs].map((m, i) => ({ m, i })).reverse().find(x => x.m.role === 'user');
    if (!lastUserIdx) return;
    this.prompt = typeof lastUserIdx.m.content === 'string' ? lastUserIdx.m.content : '';
    // Remove the last user message and everything after it
    this.messages.set(msgs.slice(0, lastUserIdx.i));
  }

  shareConversation(): void {
    if (!this.currentConversationId) return;
    this.conversationService.share(this.currentConversationId).subscribe({
      next: (result) => {
        const shareUrl = `${window.location.origin}/shared/${result.share_token}`;
        this.copyToClipboard(shareUrl).then(ok => {
          if (ok) {
            this.notifications.add('success', this.i18n.translate('chat.share.copiedTitle'), this.i18n.translate('chat.share.copiedBody'));
          } else {
            this.notifications.add('info', this.i18n.translate('chat.share.linkTitle'), shareUrl, 15000);
          }
        });
      },
      error: () => this.notifications.add('error', this.i18n.translate('common.error'), this.i18n.translate('chat.share.failed')),
    });
  }

  private async copyToClipboard(text: string): Promise<boolean> {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch { /* secure context required */ }
    // Fallback for HTTP contexts
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    try {
      const ok = document.execCommand('copy');
      document.body.removeChild(ta);
      return ok;
    } catch {
      document.body.removeChild(ta);
      return false;
    }
  }

  exportConversation(format: 'md' | 'json'): void {
    if (!this.currentConversationId) return;
    const url = `${this.appConfig.apiUrl}/conversations/${this.currentConversationId}/export?format=${format}`;
    fetch(url, { headers: this.authHeaders({ 'X-Profile-ID': this.profileService.currentId }) })
      .then(r => r.blob())
      .then(blob => {
        const ext = format === 'json' ? 'json' : 'md';
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = `conversation-${this.currentConversationId}.${ext}`;
        a.click();
        URL.revokeObjectURL(a.href);
      })
      .catch(() => this.notifications.add('error', this.i18n.translate('chat.export.failedTitle'), this.i18n.translate('chat.export.failedBody')));
  }

  startVoiceInput(): void {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const w = window as any;
    const SpeechRecognitionAPI = w.SpeechRecognition ?? w.webkitSpeechRecognition;

    if (!SpeechRecognitionAPI) return;

    if (this.isListening && this.voiceRecognition) {
      this.voiceRecognition.stop();
      return;
    }

    const recognition = new SpeechRecognitionAPI();
    recognition.lang = this.i18n.bcp47();
    recognition.interimResults = true;
    recognition.continuous = false;
    this.voiceRecognition = recognition;
    this.isListening = true;

    let base = this.prompt;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    recognition.onresult = (event: any) => {
      const transcript = Array.from<any>(event.results)
        .map((r: any) => r[0].transcript)
        .join('');
      this.prompt = base + transcript;
    };
    recognition.onend = () => {
      this.isListening = false;
      this.voiceRecognition = null;
      base = this.prompt;
    };
    recognition.onerror = () => {
      this.isListening = false;
      this.voiceRecognition = null;
    };
    recognition.start();
  }

  /** Open the native file picker for images. */
  triggerImageUpload(): void {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/jpeg,image/png,image/webp,image/gif';
    input.onchange = () => {
      const file = input.files?.[0];
      if (!file) return;
      if (file.size > 20 * 1024 * 1024) {
        this.notifications.add('error', this.i18n.translate('chat.err.fileTooBigTitle'), this.i18n.translate('chat.err.fileTooBigBody'));
        return;
      }
      const reader = new FileReader();
      reader.onload = () => {
        const dataUrl = reader.result as string;
        this.attachedImageB64 = dataUrl;
        this.attachedImageName = file.name;
      };
      reader.readAsDataURL(file);
    };
    input.click();
  }

  removeAttachedImage(): void {
    this.attachedImageB64 = null;
    this.attachedImageName = null;
  }

  onDragOver(event: DragEvent): void {
    event.preventDefault();
    event.stopPropagation();
    this.dragActive = true;
  }

  onDragLeave(event: DragEvent): void {
    event.preventDefault();
    event.stopPropagation();
    this.dragActive = false;
  }

  onDrop(event: DragEvent): void {
    event.preventDefault();
    event.stopPropagation();
    this.dragActive = false;
    const file = event.dataTransfer?.files?.[0];
    if (!file || !file.type.startsWith('image/')) {
      if (file) this.notifications.add('error', this.i18n.translate('chat.err.fileTypeTitle'), this.i18n.translate('chat.err.fileTypeBody'));
      return;
    }
    if (file.size > 20 * 1024 * 1024) {
      this.notifications.add('error', this.i18n.translate('chat.err.fileTooBigTitle'), this.i18n.translate('chat.err.fileTooBigBody'));
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      this.attachedImageB64 = reader.result as string;
      this.attachedImageName = file.name;
    };
    reader.readAsDataURL(file);
  }

  /** Handle paste events to capture pasted images. */
  onPaste(event: ClipboardEvent): void {
    const items = event.clipboardData?.items;
    if (!items) return;
    for (let i = 0; i < items.length; i++) {
      if (items[i].type.startsWith('image/')) {
        const file = items[i].getAsFile();
        if (!file) continue;
        event.preventDefault();
        const reader = new FileReader();
        reader.onload = () => {
          this.attachedImageB64 = reader.result as string;
          this.attachedImageName = file.name || 'pasted-image';
        };
        reader.readAsDataURL(file);
        break;
      }
    }
  }

  get hasSpeechRecognition(): boolean {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const w = window as any;
    return !!(w.SpeechRecognition || w.webkitSpeechRecognition);
  }

  get lastUserMessageIdx(): number {
    const msgs = this.messages();
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i].role === 'user') return i;
    }
    return -1;
  }

  get lastAssistantMessageIdx(): number {
    const msgs = this.messages();
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i].role === 'assistant') return i;
    }
    return -1;
  }

  /** Abort the in-flight streaming request and reset the UI to idle. */
  cancelStream(): void {
    if (this.streamSubscription) {
      this.streamSubscription.unsubscribe();
      this.streamSubscription = null;
    }
    this.loading = false;
    this.streaming = false;
  }

  /** Handle /imagine <prompt> — generate an image via the backend. */
  private handleImagineCommand(prompt: string): void {
    if (!prompt) return;
    this.prompt = '';
    this.loading = true;
    this.generatingImage = true;

    this.messages.update(items => [
      ...items,
      { role: 'user' as const, content: `/imagine ${prompt}`, created_at: Math.floor(Date.now() / 1000) },
      { role: 'assistant' as const, content: this.i18n.translate('chat.imagine.generating'), model: 'image-gen', created_at: Math.floor(Date.now() / 1000) },
    ]);
    this.queueScrollToBottom();

    const assistantIdx = this.messages().length - 1;

    this.chatService.generateImage(prompt).subscribe({
      next: (result) => {
        const imageDataUrl = `data:image/png;base64,${result.b64_json}`;
        this.messages.update(items =>
          items.map((m, i) =>
            i === assistantIdx
              ? { ...m, content: '', image_url: imageDataUrl, provider: result.provider, model: result.model }
              : m
          )
        );
        this.loading = false;
        this.generatingImage = false;
        this.queueScrollToBottom();
      },
      error: (err: Error) => {
        const detail = err?.message || this.i18n.translate('chat.imagine.failedBody');
        this.notifications.add('error', this.i18n.translate('chat.imagine.failedTitle'), detail);
        this.messages.update(items =>
          items.map((m, i) =>
            i === assistantIdx ? { ...m, content: `⚠ ${detail}` } : m
          )
        );
        this.loading = false;
        this.generatingImage = false;
        this.queueScrollToBottom();
      },
    });
  }

  /**
   * After a successful exchange, create or update the persisted conversation.
   * Creates a new conversation on first send, then appends the user + assistant pair.
   */
  private persistExchange(userMessage: ChatMessage, assistantIdx: number): void {
    let assistantMessage = this.messages()[assistantIdx];
    if (!assistantMessage?.content) {
      return;
    }

    // Assign IDs for branching support
    const userMsgId = userMessage.id || self.crypto?.randomUUID?.() || (Math.random().toString(36).slice(2) + Date.now().toString(36));
    const userToSave = { ...userMessage, id: userMsgId };

    if (this._pendingBranchParentId) {
      // Branching mode: only save the new assistant branch
      assistantMessage = {
        ...assistantMessage,
        parent_id: this._pendingBranchParentId,
        branch_index: this._pendingBranchIndex,
      };
      this.messages.update(items =>
        items.map((m, i) => i === assistantIdx ? { ...assistantMessage, branch_count: this._pendingBranchIndex + 1 } : m)
      );
      this._pendingBranchParentId = null;
      this._pendingBranchIndex = 0;

      // Only save the assistant branch, not the user message again
      const saveMessages = () => {
        this.conversationService
          .appendMessages(this.currentConversationId!, [assistantMessage], this.memoryEnabled())
          .subscribe({ next: () => this.loadConversationList() });
      };
      if (this.currentConversationId) {
        saveMessages();
      }
      return;
    }

    // Set parent_id on assistant message pointing to the user message
    assistantMessage = { ...assistantMessage, parent_id: userMsgId, branch_index: 0, branch_count: 1 };
    // Update in-memory messages with IDs
    this.messages.update(items =>
      items.map((m, i) => {
        if (i === assistantIdx) return assistantMessage;
        if (m === userMessage || (m.role === 'user' && m.content === userMessage.content && m.created_at === userMessage.created_at)) return userToSave;
        return m;
      })
    );

    const saveMessages = () => {
      this.conversationService
        .appendMessages(this.currentConversationId!, [userToSave, assistantMessage], this.memoryEnabled())
        .subscribe({ next: () => {
          this.loadConversationList();
        } });
    };

    if (this.currentConversationId) {
      saveMessages();
    } else {
      const title = (typeof userMessage.content === 'string' ? userMessage.content : '').slice(0, 60);
      const profileId = this.profileService.currentId;
      this.conversationService.create(title, this.model, profileId).subscribe({
        next: (conv) => {
          this.currentConversationId = conv.id;
          saveMessages();
          // Phase 19: the LLM auto-title lands a few seconds after the first
          // exchange is persisted — refresh the list to pick it up.
          setTimeout(() => this.loadConversationList(), 6000);
        },
      });
    }
  }

  /** Open the profile selector (clears current profile so the modal reappears). */
  switchProfile(): void {
    this.profileService.clear();
  }

  /** Load conversation list from backend for the active profile. */
  loadConversationList(): void {
    const profileId = this.profileService.currentId;
    this.conversationService.list(profileId).subscribe({
      next: (list) => this.conversations.set(list),
      error: () => {},
    });
  }

  /** Start a blank new chat without persisting anything until the first send. */
  newConversation(): void {
    this.currentConversationId = null;
    this.messages.set([
      {
        role: 'assistant',
        content: this.i18n.translate('chat.welcome'),
        model: 'SpiceSibyl',
        created_at: Math.floor(Date.now() / 1000),
      },
    ]);
    this.queueScrollToBottom(true);
    if (window.innerWidth < 992) {
      this.sidebarOpen = false;
    }
  }

  /** Load an existing conversation from the backend and display it. */
  closeTagPopover(): void {
    this.tagAssignConvId = null;
  }

  selectConversation(id: string): void {
    this.tagAssignConvId = null;
    if (id === this.currentConversationId) return;
    this.conversationService.get(id).subscribe({
      next: (conv) => {
        this.currentConversationId = conv.id;
        this.model = conv.model;
        this.loadPinnedMessages();

        // Build display messages: for branched messages, show only the latest branch per parent
        const allMessages = conv.messages;
        const branchGroups = new Map<string, ChatMessage[]>();
        for (const m of allMessages) {
          if (m.parent_id) {
            const group = branchGroups.get(m.parent_id) ?? [];
            group.push(m);
            branchGroups.set(m.parent_id, group);
          }
        }

        const shownParents = new Set<string>();
        const displayMessages: ChatMessage[] = [];
        for (const m of allMessages) {
          if (m.parent_id && branchGroups.has(m.parent_id)) {
            if (shownParents.has(m.parent_id)) continue;
            shownParents.add(m.parent_id);
            const siblings = branchGroups.get(m.parent_id)!;
            const latest = siblings[siblings.length - 1];
            displayMessages.push({ ...latest, branch_count: siblings.length });
          } else {
            displayMessages.push(m);
          }
        }

        this.messages.set(
          displayMessages.length ? displayMessages : [{
            role: 'assistant' as const,
            content: this.i18n.translate('chat.conversation.empty'),
            model: 'SpiceSibyl',
            created_at: Math.floor(Date.now() / 1000),
          }]
        );
        this.queueScrollToBottom(true);
        // On mobile the sidebar is an overlay — close it so the chat is visible
        if (window.innerWidth < 992) {
          this.sidebarOpen = false;
        }
      },
      error: () => this.notifications.add('error', this.i18n.translate('common.error'), this.i18n.translate('chat.err.loadConv')),
    });
  }

  /** Delete a conversation and refresh the list. */
  deleteConversation(id: string, event: Event): void {
    event.stopPropagation();
    this.conversationService.delete(id).subscribe({
      next: () => {
        if (this.currentConversationId === id) {
          this.newConversation();
        }
        this.loadConversationList();
      },
      error: () => this.notifications.add('error', this.i18n.translate('common.error'), this.i18n.translate('chat.err.deleteConv')),
    });
  }

  /** Return the display label shown in the message header for a given turn. */
  roleLabel(message: ChatMessage): string {
    if (message.role === 'assistant') {
      // Strip the provider prefix (e.g. 'ollama/qwen2.5:7b' → 'QWEN2.5:7B')
      return (message.model || 'assistant').replace(/^.*\//, '').toUpperCase();
    }
    return message.role;
  }

  /** Return the ChatModel metadata for the currently selected model, if available. */
  selectedModelMeta(): ChatModel | undefined {
    return this.models().find((item) => item.id === this.model);
  }

  /**
   * Return sanitized HTML for rendering a message bubble.
   *
   * Assistant messages: Markdown → HTML via marked, then DOMPurify sanitization.
   * User messages: plain-text HTML escape with newline-to-<br> conversion.
   */
  renderedContent(message: ChatMessage): SafeHtml {
    const raw = message.content;
    const content = typeof raw === 'string' ? raw : (raw ?? '');
    if (message.role !== 'assistant') {
      return this.sanitizer.bypassSecurityTrustHtml(this.escapeHtml(content).replace(/\n/g, '<br>'));
    }
    const html = marked.parse(content, { async: false }) as string;
    const clean = DOMPurify.sanitize(html);
    return this.sanitizer.bypassSecurityTrustHtml(clean);
  }

  setCapabilityFilter(value: string): void {
    this.capabilityFilter.set(value);
    this.userPrefs.set('capabilityFilter', value);
    this.ensureValidSelectedModel();
  }

  /** Include/exclude a provider from the model picker (persisted per user). */
  toggleProvider(providerId: string): void {
    const current = new Set(this.selectedProviders());
    if (current.has(providerId)) {
      current.delete(providerId);
    } else {
      current.add(providerId);
    }
    const updated = Array.from(current);
    this.selectedProviders.set(updated);
    this.userPrefs.set('selectedProviders', updated);
    this.ensureValidSelectedModel();
  }

  isProviderSelected(providerId: string): boolean {
    return this.selectedProviders().includes(providerId);
  }

  /** Select all / clear the provider filter. */
  setAllProviders(all: boolean): void {
    const updated = all ? this.selectableProviders().map((p) => p.id) : [];
    this.selectedProviders.set(updated);
    this.userPrefs.set('selectedProviders', updated);
    this.ensureValidSelectedModel();
  }

  /** Pause auto-scroll when the user scrolls more than 80 px above the bottom. */
  onMessagesScroll(): void {
    const container = this.messagesContainer?.nativeElement;
    if (!container) {
      return;
    }
    const threshold = 80;
    const distanceFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
    this.shouldAutoScroll = distanceFromBottom <= threshold;
  }

  private ensureValidSelectedModel(): void {
    const filtered = this.filteredModels();
    if (!filtered.length) {
      this.model = '';
      return;
    }

    if (!filtered.find((item) => item.id === this.model)) {
      this.model = filtered[0].id;
    }
    this.userPrefs.set('selectedModel', this.model);
  }

  private queueScrollToBottom(force = false): void {
    if (force || this.shouldAutoScroll) {
      this.shouldAutoScroll = true;
    }
  }

  private scrollToBottom(): void {
    const container = this.messagesContainer?.nativeElement;
    if (!container) {
      return;
    }
    container.scrollTop = container.scrollHeight;
  }

  /** Map a raw API response to a ChatMessage with telemetry fields populated. */
  private mapAssistantMessage(response: ChatCompletionResponse): ChatMessage | null {
    const choice = response?.choices?.[0];
    const message = choice?.message;
    if (!message) {
      return null;
    }

    const modelMeta = this.models().find((item) => item.id === response.model);

    return {
      role: 'assistant',
      content: typeof message.content === 'string' ? message.content : JSON.stringify(message.content),
      model: response.model,
      provider: response.metrics?.provider,
      latency_ms: response.metrics?.latency_ms,
      first_token_ms: response.metrics?.first_token_ms,
      prompt_tokens: response.usage?.prompt_tokens,
      completion_tokens: response.usage?.completion_tokens,
      total_tokens: response.usage?.total_tokens,
      tokens_per_second: response.metrics?.tokens_per_second,
      finish_reason: choice?.finish_reason,
      estimated_cost: response.metrics?.estimated_cost,
      created_at: response.created,
      capabilities: modelMeta?.capabilities ?? [],
      free: modelMeta?.free,
    };
  }

  /** Escape special HTML characters to prevent XSS in user messages. */
  private escapeHtml(value: string): string {
    return value
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }
}