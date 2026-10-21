import { BrowserRouter as Router, Routes, Route, Navigate, Outlet } from 'react-router-dom';
import Navbar from './components/Navbar';
import CreatePaste from './pages/CreatePaste';
import ViewPaste from './pages/ViewPaste';
import './App.css';

function MainLayout() {
  return (
    <div className="app-layout">
      <Navbar />

      <main className="main-content">
        <Outlet />
      </main>

      <footer className="footer">
        <p>
          Pastebin &bull; Secure, temporary text sharing with instant revocation
        </p>
      </footer>
    </div>
  );
}

export default function App() {
  return (
    <Router>
      <Routes>
        <Route element={<MainLayout />}>
          <Route path="/" element={<CreatePaste />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
        <Route path="/p/:pasteId" element={<ViewPaste />} />
      </Routes>
    </Router>
  );
}

