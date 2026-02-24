export interface User {
  id: string
  email: string
  full_name: string
  role: string
  org_id: string
  is_active: boolean
}

export interface UserProfile {
  markdown_text: string | null
  tone_settings: Record<string, unknown> | null
}

export interface TrackedPage {
  id: string
  org_id: string
  platform: string
  external_id: string | null
  url: string
  name: string
  page_type: string
  active: boolean
}

export interface Subscription {
  id: string
  tracked_page_id: string
  user_id: string
  auto_like: boolean
  auto_comment: boolean
  polling_mode: string
  tags: string[] | null
}

export interface AuditLogEntry {
  id: string
  org_id: string
  user_id: string | null
  action: string
  target_type: string | null
  target_id: string | null
  metadata_: Record<string, unknown> | null
  created_at: string
}

export interface IntegrationStatus {
  linkedin: { connected: boolean; active: boolean; has_session_cookies?: boolean }
  meta: { connected: boolean; active: boolean }
  whatsapp: { connected: boolean; active: boolean }
}

export interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
}

export interface OrgInvite {
  id: string
  org_id: string
  email: string | null
  invite_code: string
  status: string
  expires_at: string
  created_at: string
  invite_url: string
}

export interface OrgMember {
  id: string
  email: string
  full_name: string
  role: string
  is_active: boolean
  created_at: string
  integrations: string[]
}

export interface SubscribeSettings {
  auto_like: boolean
  auto_comment: boolean
  polling_mode: string
}

export interface ImportResult {
  imported: number
  skipped: number
  errors: string[]
}

export interface AnalyticsSummary {
  likes: Record<string, number>
  comments: Record<string, number>
}

export interface AutomationSettings {
  risk_profile: 'safe' | 'aggro'
  quiet_hours_start: string
  quiet_hours_end: string
  polling_interval: number
}

export interface EngagementBrief {
  id: string
  action_type: string
  status: string
  completed_at: string | null
  error_message: string | null
}

export interface PostWithEngagements {
  id: string
  url: string
  content_text: string | null
  external_post_id: string
  first_seen_at: string
  engagements: EngagementBrief[]
}

export interface PollStatus {
  last_polled_at?: string
  status: string
  posts_found?: number
  new_posts?: number
  error?: string | null
}
