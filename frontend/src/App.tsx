import { createBrowserRouter, Navigate, RouterProvider } from "react-router-dom";
import { Toaster } from "sonner";
import { AppShell } from "@/components/layout/AppShell";
import { HomePage } from "@/pages/HomePage";
import { ChatPage } from "@/pages/ChatPage";
import { IngestionPage } from "@/pages/IngestionPage";
import { TasksPage } from "@/pages/TasksPage";
import { EventLogPage } from "@/pages/EventLogPage";
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
      { path: "ingestion", element: <IngestionPage /> },
      { path: "tasks", element: <TasksPage /> },
      { path: "events", element: <EventLogPage /> },
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
