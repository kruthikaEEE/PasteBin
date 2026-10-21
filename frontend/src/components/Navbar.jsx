import { Link, useLocation } from 'react-router-dom';

export default function Navbar() {
  const location = useLocation();
  const isCreatePage = location.pathname === '/';

  return (
    <header className="navbar">
      <div className="navbar-container">
        <Link to="/" className="navbar-brand" title="Pastebin Home">
          <div className="logo-icon" aria-hidden="true">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"></path>
              <rect x="8" y="2" width="8" height="4" rx="1" ry="1"></rect>
            </svg>
          </div>
          <span className="brand-title">Pastebin</span>
        </Link>

        <nav className="navbar-actions">
          {!isCreatePage && (
            <Link to="/" className="btn-nav" id="nav-new-paste">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <line x1="12" y1="5" x2="12" y2="19"></line>
                <line x1="5" y1="12" x2="19" y2="12"></line>
              </svg>
              <span>New Paste</span>
            </Link>
          )}
        </nav>
      </div>
    </header>
  );
}
