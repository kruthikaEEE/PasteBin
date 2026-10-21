import { useState } from 'react';
import { Link } from 'react-router-dom';
import { API_BASE_URL } from '../config';

const MAX_BYTES = 100 * 1024; // 100 KB limit = 102,400 bytes
const MAX_EXPIRY_MINUTES = 30 * 24 * 60; // 30 days = 43,200 minutes

export default function CreatePaste() {
  const [content, setContent] = useState('');
  const [expiryOption, setExpiryOption] = useState('10'); // '10' | '60' | '1440' | 'custom'
  const [customValue, setCustomValue] = useState('30');
  const [customUnit, setCustomUnit] = useState('minutes'); // 'minutes' | 'hours' | 'days'

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [createdPaste, setCreatedPaste] = useState(null);
  const [copied, setCopied] = useState(false);

  // Revocation (Kill Switch) state
  const [confirmingRevoke, setConfirmingRevoke] = useState(false);
  const [revoking, setRevoking] = useState(false);
  const [revoked, setRevoked] = useState(false);
  const [revokeError, setRevokeError] = useState(null);

  // Calculate UTF-8 byte length in real time
  const currentBytes = new TextEncoder().encode(content).length;

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (loading) return; // Prevent duplicate submissions

    setError(null);
    setRevoked(false);
    setRevoking(false);
    setRevokeError(null);
    setConfirmingRevoke(false);

    // 1. Content validation
    if (!content || !content.trim()) {
      setError('Paste content cannot be empty or contain only whitespace.');
      return;
    }

    if (currentBytes > MAX_BYTES) {
      setError(
        `Paste content exceeds the 100 KB limit (${currentBytes.toLocaleString()} bytes provided, max allowed is ${MAX_BYTES.toLocaleString()} bytes).`
      );
      return;
    }

    // 2. Expiry duration calculation & validation
    let totalMinutes = 0;

    if (expiryOption !== 'custom') {
      totalMinutes = Number(expiryOption);
    } else {
      const trimmedVal = customValue.trim();

      if (!trimmedVal) {
        setError('Please enter a custom expiry duration.');
        return;
      }

      // Strictly validate positive integer (rejects negative numbers, zero, decimals, non-digits)
      if (!/^[1-9]\d*$/.test(trimmedVal)) {
        setError('Duration must be a positive whole number greater than 0.');
        return;
      }

      const num = parseInt(trimmedVal, 10);
      let multiplier = 1;
      if (customUnit === 'hours') multiplier = 60;
      if (customUnit === 'days') multiplier = 1440;

      totalMinutes = num * multiplier;

      if (totalMinutes > MAX_EXPIRY_MINUTES) {
        setError('Custom expiry duration cannot exceed 30 days (43,200 minutes).');
        return;
      }

      if (totalMinutes < 1) {
        setError('Custom expiry duration must be at least 1 minute.');
        return;
      }
    }

    setLoading(true);

    try {
      const response = await fetch(`${API_BASE_URL}/pastes`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          content: content,
          expiry_minutes: totalMinutes,
        }),
      });

      const data = await response.json();

      if (!response.ok) {
        let message = 'Failed to create paste.';
        if (data && data.detail) {
          if (Array.isArray(data.detail)) {
            message = data.detail.map((d) => d.msg || d).join(', ');
          } else {
            message = data.detail;
          }
        }
        throw new Error(message);
      }

      // Success: backend returned { id, url, created_at, expires_at, revoke_token }
      setCreatedPaste(data);
      setContent('');
      setCopied(false);
      setRevoked(false);
    } catch (err) {
      setError(err.message || 'Network error: could not connect to server.');
    } finally {
      setLoading(false);
    }
  };

  const handleCopy = async () => {
    if (!createdPaste?.url) return;
    try {
      await navigator.clipboard.writeText(createdPaste.url);
      setCopied(true);
      setTimeout(() => setCopied(false), 3000);
    } catch {
      const textarea = document.createElement('textarea');
      textarea.value = createdPaste.url;
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand('copy');
      document.body.removeChild(textarea);
      setCopied(true);
      setTimeout(() => setCopied(false), 3000);
    }
  };

  const handleRevoke = async () => {
    if (!createdPaste?.id || !createdPaste?.revoke_token || revoking || revoked) return;

    setRevoking(true);
    setRevokeError(null);

    try {
      const response = await fetch(`${API_BASE_URL}/pastes/${createdPaste.id}/revoke`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Revoke-Token': createdPaste.revoke_token,
        },
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || 'Failed to revoke paste.');
      }

      setRevoked(true);
      setConfirmingRevoke(false);
    } catch (err) {
      setRevokeError(err.message || 'Could not revoke paste. Network error.');
    } finally {
      setRevoking(false);
    }
  };

  const handleReset = () => {
    setCreatedPaste(null);
    setRevoked(false);
    setRevoking(false);
    setConfirmingRevoke(false);
    setRevokeError(null);
    setError(null);
  };

  return (
    <div className="page-container">
      <div className="page-header">
        <h1 className="page-title">Share text securely</h1>
        <p className="page-description">
          Create a temporary, tamper-evident text paste with automated expiry or instant link revocation.
        </p>
      </div>

      {/* Success banner with shareable URL & Owner Kill Switch */}
      {createdPaste && (
        <section className="result-card" id="success-banner" aria-label="Paste Creation Result">
          <div className="result-header">
            <div className="result-status">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <polyline points="20 6 9 17 4 12"></polyline>
              </svg>
              <span>Paste Created Successfully</span>
            </div>
            <button
              type="button"
              className="btn-dismiss"
              onClick={handleReset}
              aria-label="Create another paste"
              title="Close and create another"
            >
              ✕
            </button>
          </div>

          <p className="result-subtext">
            {revoked
              ? 'This link has been revoked and is permanently inaccessible.'
              : 'Share this link to allow others to view your paste before it expires.'}
          </p>

          <div className="shareable-row">
            <input
              type="text"
              readOnly
              value={createdPaste.url}
              className={`shareable-input ${revoked ? 'revoked' : ''}`}
              id="shareable-url-input"
              aria-label="Shareable Paste URL"
              onClick={(e) => !revoked && e.target.select()}
            />
            <button
              type="button"
              id="copy-link-btn"
              className={`btn-action btn-copy ${copied ? 'copied' : ''}`}
              onClick={handleCopy}
              disabled={revoked}
            >
              {copied ? (
                <>
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <polyline points="20 6 9 17 4 12"></polyline>
                  </svg>
                  <span>Copied!</span>
                </>
              ) : (
                <>
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                  </svg>
                  <span>Copy Link</span>
                </>
              )}
            </button>
          </div>

          {/* Owner Kill Switch Section */}
          {createdPaste.revoke_token && (
            <div className="killswitch-card" id="revoke-controls">
              <div className="killswitch-meta">
                <span className="killswitch-badge">Kill Switch</span>
                <span className="killswitch-session-note">
                  Owner token held in memory for this session only.
                </span>
              </div>

              {revoked ? (
                <div className="revoked-confirmed-badge" id="revoke-success-msg">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <circle cx="12" cy="12" r="10"></circle>
                    <line x1="4.93" y1="4.93" x2="19.07" y2="19.07"></line>
                  </svg>
                  <span>Link revoked. Any viewer attempting to open it will receive HTTP 410 Gone.</span>
                </div>
              ) : confirmingRevoke ? (
                <div className="killswitch-confirm-box">
                  <p className="confirm-text">
                    Revoke this link permanently? This action is irreversible.
                  </p>
                  <div className="confirm-actions">
                    <button
                      type="button"
                      className="btn-danger"
                      onClick={handleRevoke}
                      disabled={revoking}
                    >
                      {revoking ? 'Revoking...' : 'Confirm Revoke'}
                    </button>
                    <button
                      type="button"
                      className="btn-ghost"
                      onClick={() => setConfirmingRevoke(false)}
                      disabled={revoking}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <div className="killswitch-trigger-row">
                  <button
                    type="button"
                    id="revoke-paste-btn"
                    className="btn-danger-outline"
                    onClick={() => setConfirmingRevoke(true)}
                  >
                    Revoke Now
                  </button>
                  <span className="killswitch-explanation">
                    Immediately deactivates the shareable link before scheduled expiry.
                  </span>
                </div>
              )}

              {revokeError && (
                <div className="form-error-inline" id="revoke-error-msg" role="alert">
                  {revokeError}
                </div>
              )}
            </div>
          )}

          <div className="result-footer">
            {!revoked ? (
              <Link to={`/p/${createdPaste.id}`} className="link-subtle" id="view-paste-link">
                View paste directly →
              </Link>
            ) : (
              <span className="text-muted">Link deactivated</span>
            )}
            <button type="button" className="btn-secondary" onClick={handleReset}>
              + Create Another Paste
            </button>
          </div>
        </section>
      )}

      {/* Main paste creation form */}
      <form onSubmit={handleSubmit} className="paste-form" noValidate>
        <div className="form-group">
          <div className="field-header">
            <label htmlFor="paste-content" className="field-label">
              Paste Content
            </label>
            <span
              className={`counter-text ${currentBytes > MAX_BYTES ? 'limit-exceeded' : ''}`}
              id="content-byte-counter"
            >
              {(currentBytes / 1024).toFixed(1)} KB / 100 KB
            </span>
          </div>

          <textarea
            id="paste-content"
            className="paste-textarea"
            placeholder="Type or paste your text, code, or secret notes here..."
            value={content}
            onChange={(e) => {
              setContent(e.target.value);
              if (error) setError(null);
            }}
            rows={12}
            disabled={loading}
            spellCheck="false"
            required
          />
        </div>

        {/* Expiry Selector and Create Action */}
        <div className="form-action-bar">
          <div className="expiry-block">
            <label htmlFor="expiry-select" className="field-label">
              Expires After
            </label>

            <div className="expiry-controls-row">
              <div className="select-container">
                <select
                  id="expiry-select"
                  className="form-select"
                  value={expiryOption}
                  onChange={(e) => {
                    setExpiryOption(e.target.value);
                    if (error) setError(null);
                  }}
                  disabled={loading}
                >
                  <option value="10">10 minutes</option>
                  <option value="60">1 hour</option>
                  <option value="1440">24 hours</option>
                  <option value="custom">Custom</option>
                </select>
              </div>

              {/* Custom Input & Unit Selector */}
              {expiryOption === 'custom' && (
                <div className="custom-expiry-cluster" id="custom-expiry-controls">
                  <input
                    type="number"
                    id="custom-duration-input"
                    className="form-input duration-input"
                    min="1"
                    max="43200"
                    step="1"
                    value={customValue}
                    placeholder="Duration"
                    onChange={(e) => {
                      setCustomValue(e.target.value);
                      if (error) setError(null);
                    }}
                    disabled={loading}
                    aria-label="Custom duration amount"
                  />

                  <div className="select-container unit-select-container">
                    <select
                      id="custom-unit-select"
                      className="form-select unit-select"
                      value={customUnit}
                      onChange={(e) => {
                        setCustomUnit(e.target.value);
                        if (error) setError(null);
                      }}
                      disabled={loading}
                      aria-label="Custom duration unit"
                    >
                      <option value="minutes">Minutes</option>
                      <option value="hours">Hours</option>
                      <option value="days">Days</option>
                    </select>
                  </div>
                </div>
              )}
            </div>
          </div>

          <div className="submit-block">
            <button
              type="submit"
              id="create-paste-btn"
              className="btn-primary"
              disabled={loading}
            >
              {loading ? (
                <>
                  <span className="btn-spinner" aria-hidden="true"></span>
                  <span>Creating Paste...</span>
                </>
              ) : (
                <>
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <line x1="22" y1="2" x2="11" y2="13"></line>
                    <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
                  </svg>
                  <span>Create Paste</span>
                </>
              )}
            </button>
          </div>
        </div>

        {/* Validation / error banner */}
        {error && (
          <div className="form-error-banner" id="error-message" role="alert">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <circle cx="12" cy="12" r="10"></circle>
              <line x1="12" y1="8" x2="12" y2="12"></line>
              <line x1="12" y1="16" x2="12.01" y2="16"></line>
            </svg>
            <span>{error}</span>
          </div>
        )}
      </form>
    </div>
  );
}
