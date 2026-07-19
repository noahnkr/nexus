import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { ArrowRight, Moon, Sun } from "lucide-react";
import { api, type TenantSettings } from "@/lib/api";
import { supabase } from "@/lib/supabase";
import { useAuth } from "@/lib/auth";
import { useTheme } from "@/lib/theme";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/layout/PageHeader";

// Four sections, two backends: Profile and Appearance are client-side (Supabase
// Auth and the theme provider respectively — no API of ours involved), while
// Workspace goes through PATCH /api/settings. Agent instructions deliberately do
// NOT live here: they're editable next to the documents they work with, on
// /knowledge, and this page just points at them.
export function SettingsPage() {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="Settings"
        description="Your profile, this workspace, and how the assistant behaves."
      />
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-2xl space-y-6 px-4 py-6 sm:px-6">
          <ProfileSection />
          <WorkspaceSection />
          <AgentSection />
          <AppearanceSection />
        </div>
      </div>
    </div>
  );
}

function Section({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border bg-card p-4 sm:p-5">
      <h2 className="text-sm font-semibold">{title}</h2>
      {description && (
        <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>
      )}
      <div className="mt-4 space-y-4">{children}</div>
    </section>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="mb-1 block text-xs font-medium text-muted-foreground">
        {label}
      </label>
      {children}
      {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

function ProfileSection() {
  const { session } = useAuth();
  const email = session?.user?.email ?? "";

  const [displayName, setDisplayName] = useState("");
  const [savingName, setSavingName] = useState(false);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [savingPassword, setSavingPassword] = useState(false);
  const [passwordError, setPasswordError] = useState<string | null>(null);

  // Seed from the session's user_metadata once it resolves.
  useEffect(() => {
    const meta = session?.user?.user_metadata as Record<string, unknown> | undefined;
    const name = meta?.display_name;
    if (typeof name === "string") setDisplayName(name);
  }, [session?.user?.id, session?.user?.user_metadata]);

  const saveName = async () => {
    setSavingName(true);
    try {
      const { error } = await supabase.auth.updateUser({
        data: { display_name: displayName.trim() },
      });
      if (error) throw new Error(error.message);
      toast.success("Name saved");
    } catch (e) {
      toast.error(String(e instanceof Error ? e.message : e));
    } finally {
      setSavingName(false);
    }
  };

  const savePassword = async () => {
    setPasswordError(null);
    if (password !== confirm) {
      setPasswordError("The two passwords don't match.");
      return;
    }
    setSavingPassword(true);
    try {
      // Supabase enforces its own length/strength policy — surface its message
      // verbatim rather than guessing the rule client-side.
      const { error } = await supabase.auth.updateUser({ password });
      if (error) {
        setPasswordError(error.message);
        return;
      }
      setPassword("");
      setConfirm("");
      toast.success("Password updated");
    } finally {
      setSavingPassword(false);
    }
  };

  return (
    <Section title="Profile" description="How you appear in this workspace.">
      <Field label="Email">
        <Input value={email} disabled readOnly />
      </Field>

      <Field label="Display name">
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="Your name"
            className="flex-1"
          />
          <Button onClick={saveName} disabled={savingName} className="sm:w-auto">
            Save
          </Button>
        </div>
      </Field>

      <Field label="New password" hint="Leave blank to keep your current password.">
        <div className="space-y-2">
          <Input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="New password"
            autoComplete="new-password"
          />
          <Input
            type="password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            placeholder="Confirm new password"
            autoComplete="new-password"
          />
          {passwordError && (
            <p className="text-xs text-destructive">{passwordError}</p>
          )}
          <Button
            onClick={savePassword}
            disabled={savingPassword || !password || !confirm}
            variant="outline"
          >
            Update password
          </Button>
        </div>
      </Field>
    </Section>
  );
}

function WorkspaceSection() {
  const [settings, setSettings] = useState<TenantSettings | null>(null);
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .getSettings()
      .then((s) => {
        if (cancelled) return;
        setSettings(s);
        setName(s.workspace_name);
      })
      .catch((e) => !cancelled && toast.error(String(e)));
    return () => {
      cancelled = true;
    };
  }, []);

  const save = async () => {
    setSaving(true);
    try {
      const next = await api.updateSettings({ workspace_name: name.trim() });
      setSettings(next);
      setName(next.workspace_name);
      toast.success("Workspace saved");
    } catch (e) {
      toast.error(String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Section
      title="Workspace"
      description="Shared by everyone signed in to this business."
    >
      {settings === null ? (
        <Skeleton className="h-9 w-full" />
      ) : (
        <Field
          label="Workspace name"
          hint="Shown in the Home greeting. Leave blank to use the default."
        >
          <div className="flex flex-col gap-2 sm:flex-row">
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Sunrise Home Care"
              maxLength={80}
              className="flex-1"
            />
            <Button
              onClick={save}
              disabled={saving || name.trim() === settings.workspace_name}
              className="sm:w-auto"
            >
              Save
            </Button>
          </div>
        </Field>
      )}
    </Section>
  );
}

function AgentSection() {
  return (
    <Section
      title="Assistant"
      description="How the assistant writes, and what it knows."
    >
      <Link
        to="/knowledge?tab=instructions"
        className="flex items-center justify-between gap-3 rounded-md border bg-muted/30 px-3 py-2.5 text-left transition-colors hover:border-primary/40"
      >
        <span className="min-w-0">
          <span className="block text-sm font-medium">Agent instructions &amp; tone</span>
          <span className="mt-0.5 block text-xs text-muted-foreground">
            Edit these on the Knowledge page, alongside the documents the assistant
            draws on.
          </span>
        </span>
        <ArrowRight className="h-4 w-4 shrink-0 text-muted-foreground" />
      </Link>
    </Section>
  );
}

function AppearanceSection() {
  const { theme, toggleTheme } = useTheme();
  const isDark = theme === "dark";

  return (
    <Section title="Appearance" description="Applies to this browser only.">
      <div className="flex items-center justify-between gap-3">
        <span className="text-sm">{isDark ? "Dark theme" : "Light theme"}</span>
        <Button variant="outline" onClick={() => toggleTheme()}>
          {isDark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          Switch to {isDark ? "light" : "dark"}
        </Button>
      </div>
    </Section>
  );
}
