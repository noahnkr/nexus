import { createClient } from "@supabase/supabase-js";

const url = import.meta.env.VITE_SUPABASE_URL as string;
const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY as string;

// Realtime auth is set at runtime via supabase.realtime.setAuth(<backend token>)
// (see lib/api.getRealtimeToken). Until then the anon client sees zero rows,
// which is the correct fail-closed behaviour. Module 6 replaces this with real
// Supabase Auth.
export const supabase = createClient(url ?? "", anonKey ?? "", {
  auth: { persistSession: false, autoRefreshToken: false },
});
