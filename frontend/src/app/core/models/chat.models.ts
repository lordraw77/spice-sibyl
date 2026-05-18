export interface ChatMessage {
  role: 'system' | 'user' | 'assistant' | 'tool';
  content: string;
  model?: string;
  provider?: string;
  latency_ms?: number;
  first_token_ms?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
  tokens_per_second?: number;
  finish_reason?: string;
  estimated_cost?: number;
  created_at?: number;
  capabilities?: string[];
  free?: boolean;
}

export interface ChatCompletionRequest {
  model: string;
  messages: ChatMessage[];
  stream?: boolean;
  temperature?: number;
}

export interface ChatUsage {
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
}

export interface ChatMetrics {
  latency_ms?: number;
  first_token_ms?: number;
  tokens_per_second?: number;
  provider?: string;
  estimated_cost?: number;
}

export interface ChatCompletionResponse {
  id: string;
  object: string;
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

export interface ProviderSummary {
  id: string;
  label: string;
  enabled: boolean;
  configured: boolean;
  model_count: number;
  capabilities: string[];
}
