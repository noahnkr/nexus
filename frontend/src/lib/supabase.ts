import { createClient } from "@supabase/supabase-js";

const url = import.meta.env.VITE_SUPABASE_URL as string;
const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY as string;

// Real Supabase Auth (Module 6): the session is persisted and auto-refreshed, and
// supabase-js forwards its access token to both PostgREST and Realtime. The token
// carries app_metadata.tenant_id, so RLS scopes every subscription to the tenant
// with no extra wiring. `authFetch` (lib/api) reads the same session for /api calls.
export const supabase = createClient(url ?? "", anonKey ?? "", {
  auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
});
