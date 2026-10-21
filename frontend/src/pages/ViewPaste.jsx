import { useState, useEffect } from 'react';
import { useParams } from 'react-router-dom';
import { API_BASE_URL } from '../config';

export default function ViewPaste() {
  const { pasteId } = useParams();
  const [paste, setPaste] = useState(null);
  const [loading, setLoading] = useState(true);
  const [errorStatus, setErrorStatus] = useState(null); // '404' | '410_revoked' | '410_expired' | 'error'
  const [errorMessage, setErrorMessage] = useState(null);

  useEffect(() => {
    let isCurrent = true;

    const fetchPaste = async () => {
      // Clear all existing paste and error state before fetching
      setLoading(true);
      setPaste(null);
      setErrorStatus(null);
      setErrorMessage(null);

      try {
        const response = await fetch(`${API_BASE_URL}/pastes/${pasteId}`);

        if (!isCurrent) return; // Prevent race conditions

        if (response.status === 404) {
          setErrorStatus('404');
          setErrorMessage('Paste not found');
          return;
        }

        if (response.status === 410) {
          const data = await response.json().catch(() => ({}));
          if (!isCurrent) return;

          if (data?.detail === 'Paste has been revoked' || data?.detail?.includes('revoked')) {
            setErrorStatus('410_revoked');
            setErrorMessage('This paste has been revoked');
          } else {
            setErrorStatus('410_expired');
            setErrorMessage('Time limit exceeded');
          }
          return;
        }

        if (!response.ok) {
          throw new Error('Unable to retrieve paste from the server.');
        }

        const data = await response.json();
        if (isCurrent) {
          setPaste(data);
        }
      } catch (err) {
        if (isCurrent) {
          setErrorStatus('error');
          setErrorMessage(err.message || 'Unable to connect to the server. Please verify your connection.');
        }
      } finally {
        if (isCurrent) {
          setLoading(false);
        }
      }
    };

    if (pasteId) {
      fetchPaste();
    }

    return () => {
      isCurrent = false;
    };
  }, [pasteId]);

  return (
    <div className="minimal-view-container">
      {/* Loading State */}
      {loading && (
        <div className="minimal-status-box" id="loading-state" aria-live="polite">
          <div className="minimal-spinner" aria-hidden="true"></div>
          <p className="minimal-status-text">Loading...</p>
        </div>
      )}

      {/* HTTP 404 Not Found State */}
      {!loading && errorStatus === '404' && (
        <div className="minimal-status-box" id="paste-not-found-state" role="alert">
          <p className="minimal-status-title" id="not-found-heading">Paste not found</p>
        </div>
      )}

      {/* HTTP 410 Gone: Revoked State */}
      {!loading && errorStatus === '410_revoked' && (
        <div className="minimal-status-box" id="paste-revoked-state" role="alert">
          <p className="minimal-status-title" id="revoked-heading">This paste has been revoked</p>
        </div>
      )}

      {/* HTTP 410 Gone: Expired State */}
      {!loading && errorStatus === '410_expired' && (
        <div className="minimal-status-box" id="paste-expired-state" role="alert">
          <p className="minimal-status-title" id="expired-heading">Time limit exceeded</p>
        </div>
      )}

      {/* Generic Network / Server Error */}
      {!loading && errorStatus === 'error' && (
        <div className="minimal-status-box" id="paste-error-state" role="alert">
          <p className="minimal-status-title">Unable to load paste</p>
          <p className="minimal-status-desc">{errorMessage}</p>
        </div>
      )}

      {/* HTTP 200 OK: Active Unexpired Paste - Only the message box */}
      {!loading && paste && !errorStatus && (
        <div className="minimal-message-box" id="paste-view-display">
          <pre className="minimal-message-content" id="paste-content-display">
            {paste.content}
          </pre>
        </div>
      )}
    </div>
  );
}

