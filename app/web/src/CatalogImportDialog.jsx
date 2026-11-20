import { useEffect, useRef, useState } from 'react'

export default function CatalogImportDialog({
  defaultCropsBase = '',
  busy = false,
  onClose,
  onImport,
}) {
  const [cropsBase, setCropsBase] = useState(defaultCropsBase)
  const [file, setFile] = useState(null)
  const [error, setError] = useState('')
  const fileRef = useRef(null)

  useEffect(() => {
    setCropsBase(defaultCropsBase)
  }, [defaultCropsBase])

  useEffect(() => {
    function onKey(e) {
      if (e.key === 'Escape' && !busy) onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [busy, onClose])

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')
    if (!file) {
      setError('Choose a CSV file')
      return
    }
    if (!cropsBase.trim()) {
      setError('Enter the base folder for cover crops')
      return
    }
    try {
      await onImport(file, cropsBase.trim())
      onClose()
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
        aria-labelledby="import-catalog-title"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="modal-header">
          <h2 id="import-catalog-title">Import catalog CSV</h2>
          <button type="button" className="ghost" onClick={onClose} disabled={busy}>
            Close
          </button>
        </header>
        <form className="modal-form" onSubmit={handleSubmit}>
          <label>
            CSV file
            <div className="file-row">
              <button
                type="button"
                disabled={busy}
                onClick={() => fileRef.current?.click()}
              >
                Choose CSV…
              </button>
              <span className="muted small">
                {file ? file.name : 'No file chosen'}
              </span>
              <input
                ref={fileRef}
                type="file"
                accept=".csv,text/csv"
                hidden
                onChange={(e) => setFile(e.target.files?.[0] || null)}
              />
            </div>
          </label>

          <label>
            Crop base folder
            <input
              value={cropsBase}
              onChange={(e) => setCropsBase(e.target.value)}
              disabled={busy}
              placeholder="/path/to/project or /path/to/crops"
              required
            />
          </label>
          <p className="muted small">
            Folder on this machine used to resolve Cover Crop paths from the
            CSV (project root, an output run folder, or a crops directory).
          </p>

          {error ? <div className="form-error">{error}</div> : null}

          <div className="modal-actions">
            <button type="button" className="ghost" onClick={onClose} disabled={busy}>
              Cancel
            </button>
            <button type="submit" className="primary" disabled={busy}>
              {busy ? 'Importing…' : 'Import'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
