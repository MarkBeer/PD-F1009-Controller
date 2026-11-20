import { useEffect, useState } from 'react'

export default function SelectPlayerDialog({
  initialUrl = '',
  busy = false,
  onClose,
  onSave,
  onTest,
}) {
  const [url, setUrl] = useState(initialUrl)
  const [error, setError] = useState('')

  useEffect(() => {
    setUrl(initialUrl)
  }, [initialUrl])

  useEffect(() => {
    function onKey(e) {
      if (e.key === 'Escape' && !busy) onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [busy, onClose])

  async function handleSave(e) {
    e.preventDefault()
    setError('')
    try {
      await onSave(url.trim())
      onClose()
    } catch (err) {
      setError(err.message || String(err))
    }
  }

  async function handleTest() {
    setError('')
    try {
      await onTest(url.trim())
    } catch (err) {
      setError(err.message || String(err))
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="select-player-title"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="modal-header">
          <h2 id="select-player-title">Select player</h2>
          <button type="button" className="ghost" onClick={onClose} disabled={busy}>
            Close
          </button>
        </header>
        <form className="modal-form" onSubmit={handleSave}>
          <label>
            Arduino URL
            <input
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              disabled={busy}
              placeholder="http://192.168.1.50"
            />
          </label>
          <p className="muted small">
            Base URL for the PD-F1009 IR bridge. Leave empty for local-only play
            commands.
          </p>

          {error ? <div className="form-error">{error}</div> : null}

          <div className="modal-actions">
            <button
              type="button"
              className="ghost"
              disabled={busy || !url.trim()}
              onClick={handleTest}
            >
              Test
            </button>
            <button type="button" className="ghost" onClick={onClose} disabled={busy}>
              Cancel
            </button>
            <button type="submit" className="primary" disabled={busy}>
              {busy ? 'Saving…' : 'Save'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
