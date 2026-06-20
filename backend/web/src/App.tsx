import { Route, Routes } from "react-router-dom";
import { AuthProvider, RequireAuth } from "./auth";
import { Layout } from "./components/Layout";
import { Login } from "./pages/Login";
import { Discover } from "./pages/Discover";
import { Search } from "./pages/Search";
import { ArtistDetail } from "./pages/ArtistDetail";
import { Requests } from "./pages/Requests";
import { Settings } from "./pages/Settings";

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          element={
            <RequireAuth>
              <Layout />
            </RequireAuth>
          }
        >
          <Route index element={<Discover />} />
          <Route path="search" element={<Search />} />
          <Route path="artist/:id" element={<ArtistDetail />} />
          <Route path="requests" element={<Requests />} />
          <Route path="settings" element={<Settings />} />
        </Route>
      </Routes>
    </AuthProvider>
  );
}
