import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import { ToastProvider } from "./components/ui";
import Dashboard from "./pages/Dashboard";
import Documents from "./pages/Documents";
import Domains from "./pages/Domains";
import Knowledge from "./pages/Knowledge";
import Pipeline from "./pages/Pipeline";
import Review from "./pages/Review";
import SearchAsk from "./pages/SearchAsk";
import Sources from "./pages/Sources";

export default function App() {
  return (
    <ToastProvider>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="knowledge" element={<Knowledge />} />
          <Route path="search" element={<SearchAsk />} />
          <Route path="review" element={<Review />} />
          <Route path="sources" element={<Sources />} />
          <Route path="documents" element={<Documents />} />
          <Route path="pipeline" element={<Pipeline />} />
          <Route path="domains" element={<Domains />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </ToastProvider>
  );
}
