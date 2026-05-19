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
  content: string;
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

/** Body sent to POST /api/v1/chat/completions */
export interface ChatCompletionRequest {
  model: string;
  messages: ChatMessage[];
  stream?: boolean;
  temperature?: number;
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

/** Per-provider summary included in the GET /api/v1/models response */
export interface ProviderSummary {
  id: string;
  label: string;
  enabled: boolean;
  configured: boolean;
  model_count: number;
  capabilities: string[];
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
