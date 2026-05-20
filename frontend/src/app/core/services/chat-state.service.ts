import { Injectable, signal } from '@angular/core';
import { ChatMessage } from '../models/chat.models';

@Injectable({ providedIn: 'root' })
export class ChatStateService {
  readonly messages = signal<ChatMessage[]>([
    {
      role: 'assistant',
      content: 'Welcome to SpiceSibyl. Select a model and start chatting.',
      model: 'SpiceSibyl',
      created_at: Math.floor(Date.now() / 1000),
    },
  ]);
  readonly loading = signal(false);
  readonly streaming = signal(false);
  currentConversationId: string | null = null;
  // Tracks the last active profile ID to distinguish a profile switch
  // from a plain component re-mount (e.g. navigation away and back).
  lastActiveProfileId: string | null = null;
}
