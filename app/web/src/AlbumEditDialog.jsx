import { useEffect, useRef, useState } from 'react'

export default function AlbumEditDialog({
  album,
  onClose,
  onSave,
  onUploadCover,
  onSyncDiscogs,
}) {
  const [artist, setArtist] = useState(album.artist || '')
  const [title, setTitle] = useState(album.title || '')
  const [discogsId, setDiscogsId] = useState(
    album.discogs_release_id != null ? String(album.discogs_release_id) : '',
  )
  const [fetchFromDiscogs, setFetchFromDiscogs] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [preview, setPreview] = useState(null)
  const [file, setFile] = useState(null)
  const fileRef = useRef(null)

  useEffect(() => {
    function onKey(e) {
      if (e.key === 'Escape' && !busy) onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [busy, onClose])

  useEffect(() => {
    return () => {
      if (preview) URL.revokeObjectURL(preview)
    }
  }, [preview])

  function pickFile(e) {
    const next = e.target.files?.[0] || null
    if (preview) URL.revokeObjectURL(preview)
    setFile(next)
    setPreview(next ? URL.createObjectURL(next) : null)
  }

  async function handleSubmit(e) {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      const idText = discogsId.trim()
      let releaseId = null
      if (idText) {
        const n = Number(idText)
        if (!Number.isInteger(n) || n < 1) {
          throw new Error('Discogs release id must be a positive integer')
        }
        releaseId = n
      }

      if (fetchFromDiscogs) {
        if (!releaseId) {
          throw new Error('Enter a Discogs release id to fetch metadata')
        }
        if (!onSyncDiscogs) {
          throw new Error('Discogs sync is unavailable')
        }
        await onSyncDiscogs(album.id, { releaseId })
        if (file) {
          await onUploadCover(album.id, file)
        }
        onClose()
        return
      }

      const trimmedArtist = artist.trim()
      const trimmedTitle = title.trim()
      if (!trimmedArtist || !trimmedTitle) {
        throw new Error('Artist and album title are required')
      }
      const payload = {
        artist: trimmedArtist,
        title: trimmedTitle,
      }
      if (!releaseId) {
        payload.clear_discogs_release_id = true
      } else {
        payload.discogs_release_id = releaseId
      }
      await onSave(album.id, payload)
      if (file) {
        await onUploadCover(album.id, file)
      }
      onClose()
    } catch (err) {
      setError(err.message || String(err))
    } finally {
      setBusy(false)
    }
  }

  const coverSrc = preview || `${album.cover_url}?t=${Date.now()}`

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="edit-album-title"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="modal-header">
          <h2 id="edit-album-title">Edit album</h2>
          <button type="button" className="ghost" onClick={onClose} disabled={busy}>
            Close
          </button>
        </header>
        <form className="modal-form" onSubmit={handleSubmit}>
          <div className="edit-cover-row">
            <img src={coverSrc} alt="" className="edit-cover" />
            <div>
              <button
                type="button"
                disabled={busy || fetchFromDiscogs}
                onClick={() => fileRef.current?.click()}
              >
                Upload new cover
              </button>
              <input
                ref={fileRef}
                type="file"
                accept="image/jpeg,.jpg,.jpeg"
                hidden
                onChange={pickFile}
              />
              <p className="muted small">JPEG only</p>
              {file ? <p className="muted small">{file.name}</p> : null}
            </div>
          </div>

          <p className="catalog-id-label">
            Catalog ID <span>{album.id}</span>
          </p>
          <label>
            Artist
            <input
              value={artist}
              onChange={(e) => setArtist(e.target.value)}
              disabled={busy || fetchFromDiscogs}
              required={!fetchFromDiscogs}
            />
          </label>
          <label>
            Album
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              disabled={busy || fetchFromDiscogs}
              required={!fetchFromDiscogs}
            />
          </label>
          <label>
            Discogs release ID
            <input
              value={discogsId}
              onChange={(e) => setDiscogsId(e.target.value)}
              disabled={busy}
              inputMode="numeric"
              placeholder="e.g. 36031354"
            />
          </label>

          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={fetchFromDiscogs}
              onChange={(e) => setFetchFromDiscogs(e.target.checked)}
              disabled={busy || !onSyncDiscogs}
            />
            <span>
              Fetch artist, album, track list, and cover from Discogs
            </span>
          </label>
          {fetchFromDiscogs ? (
            <p className="muted small">
              Replaces this album’s metadata and tracks from the release ID.
              Slot assignments are kept when disc numbers still match.
            </p>
          ) : null}

          {error ? <div className="form-error">{error}</div> : null}

          <div className="modal-actions">
            <button type="button" className="ghost" onClick={onClose} disabled={busy}>
              Cancel
            </button>
            <button type="submit" className="primary" disabled={busy}>
              {busy
                ? fetchFromDiscogs
                  ? 'Fetching…'
                  : 'Saving…'
                : fetchFromDiscogs
                  ? 'Fetch & save'
                  : 'Save'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
