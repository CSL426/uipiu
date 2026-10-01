import { useEffect, useState } from "react";
import { AdminAccess } from "./AdminAccess";
import { getMode } from "./lib/navigation";
import { AdminPage } from "./pages/AdminPage";
import { PublicPage } from "./pages/PublicPage";

export function App() {
  const [mode, setMode] = useState<"public" | "admin">(() => getMode());

  useEffect(() => {
    const handlePopState = () => setMode(getMode());
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  if (mode === "admin") {
    return <AdminAccess><AdminPage /></AdminAccess>;
  }

  return <PublicPage />;
}
