import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { Toaster } from "sonner";
import { AppShell } from "@/components/layout/AppShell";
import { ChatPage } from "@/pages/ChatPage";
import { IngestionPage } from "@/pages/IngestionPage";

const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <ChatPage /> },
      { path: "ingestion", element: <IngestionPage /> },
    ],
  },
]);

export default function App() {
  return (
    <>
      <RouterProvider router={router} />
      <Toaster richColors position="top-right" />
    </>
  );
}
