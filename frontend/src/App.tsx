import { createBrowserRouter, Navigate, RouterProvider } from "react-router-dom";
import { Toaster } from "sonner";
import { AppShell } from "@/components/layout/AppShell";
import { HomePage } from "@/pages/HomePage";
import { ChatPage } from "@/pages/ChatPage";
import { KnowledgePage } from "@/pages/KnowledgePage";
import { TasksPage } from "@/pages/TasksPage";
import { LeadsPage } from "@/pages/LeadsPage";
import { LeadProfilePage } from "@/pages/LeadProfilePage";
import { CaregiversPage } from "@/pages/CaregiversPage";
import { CaregiverProfilePage } from "@/pages/CaregiverProfilePage";
import { SchedulePage } from "@/pages/SchedulePage";
import { StageSequencePage } from "@/pages/StageSequencePage";
import { EventLogPage } from "@/pages/EventLogPage";
import { AutomationsPage } from "@/pages/AutomationsPage";
import { AutomationDetailPage } from "@/pages/AutomationDetailPage";
import { AutomationBuilderPage } from "@/pages/AutomationBuilderPage";
import { SettingsPage } from "@/pages/SettingsPage";
import { LoginPage } from "@/pages/LoginPage";
import { RequireAuth } from "@/lib/auth";
import { useTheme } from "@/lib/theme";

const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  {
    path: "/",
    element: (
      <RequireAuth>
        <AppShell />
      </RequireAuth>
    ),
    children: [
      { index: true, element: <HomePage /> },
      { path: "chat", element: <ChatPage /> },
      { path: "knowledge", element: <KnowledgePage /> },
      // Old links and bookmarks from when this was the Ingestion page.
      { path: "ingestion", element: <Navigate to="/knowledge" replace /> },
      { path: "tasks", element: <TasksPage /> },
      { path: "leads", element: <LeadsPage /> },
      { path: "leads/stages/:stage/sequence", element: <StageSequencePage view="leads" /> },
      { path: "leads/:id", element: <LeadProfilePage /> },
      { path: "caregivers", element: <CaregiversPage /> },
      { path: "caregivers/stages/:stage/sequence", element: <StageSequencePage view="caregivers" /> },
      { path: "caregivers/:id", element: <CaregiverProfilePage /> },
      { path: "schedule", element: <SchedulePage /> },
      { path: "automations", element: <AutomationsPage /> },
      { path: "automations/new", element: <AutomationBuilderPage /> },
      { path: "automations/:id", element: <AutomationDetailPage /> },
      { path: "automations/:id/edit", element: <AutomationBuilderPage /> },
      { path: "events", element: <EventLogPage /> },
      { path: "settings", element: <SettingsPage /> },
      // Stale bookmarks (old "/" was Chat) and unknown paths land on Home.
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
]);

export default function App() {
  const { theme } = useTheme();
  return (
    <>
      <RouterProvider router={router} />
      <Toaster richColors position="top-right" theme={theme} />
    </>
  );
}
