// Session layer over Supabase Auth. AuthProvider owns the current session and a
// loading flag; useAuth exposes them plus signOut. RequireAuth gates the app shell
// behind a real session, redirecting to /login otherwise. Every /api call rides
// the session token via authFetch (lib/api); Realtime rides it via supabase-js.
import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { Navigate, useLocation } from "react-router-dom";
import type { Session } from "@supabase/supabase-js";
import { supabase } from "./supabase";

interface AuthContextValue {
  session: Session | null;
  loading: boolean;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

// The name to greet someone by: the display name they set in Settings, falling
// back to the local part of their email, then a neutral "there". Settings writes
// it to Supabase `user_metadata.display_name`, so it rides the session — no fetch.
export function displayName(session: Session | null): string {
  const meta = session?.user?.user_metadata as Record<string, unknown> | undefined;
  const name = typeof meta?.display_name === "string" ? meta.display_name.trim() : "";
  if (name) return name;
  return (session?.user?.email ?? "").split("@")[0] || "there";
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Initial read (restores a persisted session), then subscribe to changes so
    // sign-in/out and token refresh keep the context in sync.
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setLoading(false);
    });
    const { data: sub } = supabase.auth.onAuthStateChange((_event, next) => {
      setSession(next);
    });
    return () => sub.subscription.unsubscribe();
  }, []);

  const value: AuthContextValue = {
    session,
    loading,
    signOut: async () => {
      await supabase.auth.signOut();
    },
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}

// Route guard: splash while the session resolves, redirect to /login when signed
// out (remembering where the user was headed), render the app when signed in.
export function RequireAuth({ children }: { children: ReactNode }) {
  const { session, loading } = useAuth();
  const location = useLocation();

  if (loading) {
    return (
      <div className="flex h-screen w-full items-center justify-center text-sm text-muted-foreground">
        Loading…
      </div>
    );
  }
  if (!session) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }
  return <>{children}</>;
}
