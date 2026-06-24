/**
 * Core domain types for the SpiceSibyl chat API.
 *
 * These interfaces mirror the backend Pydantic schemas so that HTTP responses
 * deserialize with full type safety.  ChatMessage is intentionally extended with
 * telemetry fields (latency, token counts, cost) that the backend attaches to
 * every assistant message.
 */

/** A single conversation turn.  Extra fields are populated only on assistant replies. */
export interface ChatMessage {
  role: 'system' | 'user' | 'assistant' | 'tool';
  content: string | null;
  /** UI-only: base64 image attached by the user (vision input) */
  image_b64?: string;
  /** UI-only: URL of a generated image (text-to-image result) */
  image_url?: string;
  /** UI-only: tool call/result events attached to this assistant message */
  tool_events?: ToolEvent[];
  model?: string;
  provider?: string;
  /** Total round-trip latency in milliseconds */
  latency_ms?: number;
  /** Time to first token in milliseconds */
  first_token_ms?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
  tokens_per_second?: number;
  finish_reason?: string;
  estimated_cost?: number;
  /** Unix timestamp (seconds) when the message was created */
  created_at?: number;
  capabilities?: string[];
  free?: boolean;
}

/** Tool call inside an assistant message */
export interface ToolCallFunction { name: string; arguments: string; }
export interface ToolCall { id: string; type: string; function: ToolCallFunction; }

/** Tool definition sent in the request */
export interface ToolFunction { name: string; description: string; parameters: Record<string, unknown>; }
export interface ToolDefinition { type: string; function: ToolFunction; }

/** UI-only tool event attached to an assistant message for display */
export interface ToolEvent {
  kind: 'call' | 'result';
  id: string;
  name: string;
  arguments?: Record<string, unknown>;
  result?: string;
}

/** Body sent to POST /api/v1/chat/completions */
export interface ChatCompletionRequest {
  model: string;
  messages: ChatMessage[];
  stream?: boolean;
  temperature?: number;
  max_tokens?: number;
  tools?: ToolDefinition[];
}

/** Token consumption reported by the provider */
export interface ChatUsage {
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
}

/** Gateway-level performance metrics returned alongside every completion */
export interface ChatMetrics {
  latency_ms?: number;
  first_token_ms?: number;
  tokens_per_second?: number;
  provider?: string;
  estimated_cost?: number;
}

/** Full response envelope from POST /api/v1/chat/completions */
export interface ChatCompletionResponse {
  id: string;
  object: string;
  /** Unix timestamp (seconds) */
  created: number;
  model: string;
  choices: Array<{
    index: number;
    finish_reason?: string;
    message: ChatMessage;
  }>;
  usage?: ChatUsage;
  metrics?: ChatMetrics;
}

/** Model entry returned by GET /api/v1/models */
export interface ChatModel {
  id: string;
  object: string;
  owned_by: string;
  label?: string;
  provider?: string;
  configured?: boolean;
  default?: boolean;
  free?: boolean;
  capabilities?: string[];
}

/** Full provider status returned by GET /api/v1/providers */
export interface ProviderStatus {
  id: string;
  label: string;
  enabled: boolean;
  configured: boolean;
  key_hint: string | null;
  model_count: number;
  capabilities: string[];
  docs_url: string | null;
}

/** Result of POST /api/v1/providers/{id}/test */
export interface ProviderTestResult {
  provider_id: string;
  ok: boolean;
  latency_ms: number | null;
  model_count: number | null;
  error: string | null;
}

/** A single SSE event emitted by the streaming endpoint */
export interface ChatStreamEvent {
  event: string;
  data: Record<string, unknown>;
}

/** Response from POST /api/v1/images/generations */
export interface ImageGenerationResponse {
  b64_json: string;
  provider: string;
  model: string;
}

/** Per-provider summary included in the GET /api/v1/models response */
export interface ProviderSummary {
  id: string;
  label: string;
  enabled: boolean;
  configured: boolean;
  model_count: number;
  capabilities: string[];
}

/** A named profile — no password, just an identity stored in localStorage */
export interface Profile {
  id: string;
  name: string;
  created_at: number;
}

/** Conversation metadata returned by GET /api/v1/conversations */
export interface ConversationSummary {
  id: string;
  title: string;
  model: string;
  created_at: number;
  updated_at: number;
}

/** Full conversation including message history */
export interface Conversation extends ConversationSummary {
  messages: ChatMessage[];
}

/** Result item returned by GET /api/v1/conversations/search */
export interface SearchResult {
  id: string;
  title: string;
  model: string;
  updated_at: number;
  snippet: string;
}

/** Telegram bot runtime counters (in-memory, reset on restart) */
export interface TelegramStats {
  enabled: boolean;
  active_chats: number;
  messages_received: number;
  messages_sent: number;
  errors: number;
}

/** Aggregate usage stats returned by GET /api/v1/stats */
export interface GlobalStats {
  total_conversations: number;
  total_messages: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_tokens: number;
  total_cost: number;
}

export interface ProfileSummary {
  profile_id: string;
  profile_name: string;
  total_conversations: number;
  total_messages: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_tokens: number;
  total_cost: number;
}

export interface ProfileSlice {
  profile_id: string;
  profile_name: string;
  message_count: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  estimated_cost: number;
}

export interface ProviderStats {
  provider: string | null;
  message_count: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  estimated_cost: number;
  avg_latency_ms: number | null;
  avg_tokens_per_second: number | null;
  by_profile: ProfileSlice[];
}

export interface ModelStats {
  model: string | null;
  provider: string | null;
  message_count: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  estimated_cost: number;
  avg_latency_ms: number | null;
  avg_tokens_per_second: number | null;
  by_profile: ProfileSlice[];
}

export interface UsageStats {
  global_stats: GlobalStats;
  by_profile: ProfileSummary[];
  by_provider: ProviderStats[];
  by_model: ModelStats[];
  telegram?: TelegramStats;
}
