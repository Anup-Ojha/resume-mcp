-- Supabase Schema for Resume Bot

-- 1. Users Table
CREATE TABLE IF NOT EXISTS public.users (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  phone         TEXT UNIQUE NOT NULL,          -- WhatsApp phone number (E.164 format)
  name          TEXT,
  created_at    TIMESTAMPTZ DEFAULT now(),
  last_seen_at  TIMESTAMPTZ DEFAULT now(),
  is_active     BOOLEAN DEFAULT TRUE
);

-- 2. Resume Sessions Table
CREATE TABLE IF NOT EXISTS public.resume_sessions (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         UUID REFERENCES public.users(id) ON DELETE CASCADE,
  session_type    TEXT NOT NULL,                -- 'generate' | 'customize' | 'parse_jd'
  raw_input       JSONB,                        -- text-based resume inputs
  jd_text         TEXT,                         -- raw job description text
  pdf_filename    TEXT,                         -- filename on server
  status          TEXT DEFAULT 'pending',       -- 'pending' | 'processing' | 'done' | 'failed'
  created_at      TIMESTAMPTZ DEFAULT now(),
  updated_at      TIMESTAMPTZ DEFAULT now()
);

-- Index for performance
CREATE INDEX IF NOT EXISTS idx_resume_sessions_user_id ON public.resume_sessions(user_id);

-- Enable RLS
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.resume_sessions ENABLE ROW LEVEL SECURITY;

-- Service Role Policies (Full access for backend)
CREATE POLICY "service_role_all" ON public.users FOR ALL USING (true);
CREATE POLICY "service_role_all" ON public.resume_sessions FOR ALL USING (true);
