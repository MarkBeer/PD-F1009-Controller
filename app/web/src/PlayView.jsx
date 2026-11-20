import { useCallback, useMemo, useState } from 'react'
import { useCatalog } from './CatalogContext'
import Toast from './Toast'

function discLabel(disc) {
  const total = disc.disc_count_in_set || 1
  if (total <= 1) return null
  return `Disc ${disc.disc_number} of ${total}`
}

export default function PlayView() {
  const { library, play, control, loading, error, coverUrl } = useCatalog()
  const [query, setQuery] = useState('')
  const [selectedId, setSelectedId] = useState(null)
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)
  const dismissStatus = useCallback(() => setStatus(''), [])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return library
    return library.filter((disc) => {
      if (disc.artist?.toLowerCase().includes(q)) return true
      if (disc.album_title?.toLowerCase().includes(q)) return true
      if (String(disc.slot_number).includes(q)) return true
      return (disc.tracks || []).some(
        (t) =>
          t.title.toLowerCase().includes(q) ||
          t.artist.toLowerCase().includes(q),
      )
    })
  }, [library, query])

  const selected = library.find((d) => d.id === selectedId) || null

  async function handlePlay(discId, trackId = null) {
    setBusy(true)
    setStatus('')
    try {
      const result = await play(discId, trackId)
      setStatus(result.message)
    } catch (err) {
      setStatus(err.message || String(err))
    } finally {
      setBusy(false)
    }
  }

  async function handleControl(action) {
    setBusy(true)
    setStatus('')
    try {
      const result = await control(action)
      setStatus(result.message)
    } catch (err) {
      setStatus(err.message || String(err))
    } finally {
      setBusy(false)
    }
  }

  if (loading) {
    return <div className="page-status">Loading library…</div>
  }
  if (error) {
    return <div className="page-status error">{error}</div>
  }

  return (
    <div className="play-view">
      <header className="view-header">
        <div>
          <h1>Play</h1>
          <p className="muted">
            Browse discs currently loaded in the changer. Commands go to the
            Arduino when its URL is set on the Load page.
          </p>
        </div>
        <label className="search">
          <span className="sr-only">Search loaded discs</span>
          <input
            type="search"
            placeholder="Search loaded artist, album, track…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
      </header>

      <Toast message={status} onDismiss={dismissStatus} />

      {library.length === 0 ? (
        <div className="empty-state">
          <p>No discs are loaded yet.</p>
          <p className="muted">Use the Load tab to assign catalog discs to slots.</p>
        </div>
      ) : (
        <>
          <div className="transport-controls" aria-label="Player controls">
            <button
              type="button"
              disabled={busy}
              onClick={() => handleControl('prev')}
            >
              Prev
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => handleControl('pause')}
            >
              Pause
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => handleControl('stop')}
            >
              Stop
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => handleControl('next')}
            >
              Next
            </button>
          </div>

          <div className="play-layout">
            <section className="cover-grid" aria-label="Loaded discs">
              {filtered.map((disc) => (
                <button
                  key={disc.id}
                  type="button"
                  className={`cover-card ${selectedId === disc.id ? 'selected' : ''}`}
                  onClick={() => setSelectedId(disc.id)}
                >
                  <img src={coverUrl(disc)} alt="" loading="lazy" />
                  <div className="cover-meta">
                    <strong>{disc.album_title}</strong>
                    <span>{disc.artist}</span>
                    <span className="badge">
                      Slot {disc.slot_number}
                      {discLabel(disc) ? ` · ${discLabel(disc)}` : ''}
                    </span>
                  </div>
                </button>
              ))}
              {filtered.length === 0 ? (
                <p className="muted">No loaded discs match that search.</p>
              ) : null}
            </section>

            <aside className="detail-panel">
              {selected ? (
                <>
                  <div className="detail-hero">
                    <img src={coverUrl(selected)} alt="" />
                    <div>
                      <h2>{selected.album_title}</h2>
                      <p>{selected.artist}</p>
                      <p className="muted">
                        Slot {selected.slot_number}
                        {discLabel(selected) ? ` · ${discLabel(selected)}` : ''}
                      </p>
                      <button
                        type="button"
                        className="primary"
                        disabled={busy}
                        onClick={() => handlePlay(selected.id)}
                      >
                        Play disc
                      </button>
                    </div>
                  </div>
                  <ol className="track-list">
                    {(selected.tracks || []).map((track) => (
                      <li key={track.id}>
                        <div>
                          <span className="track-num">{track.track_number}.</span>{' '}
                          <strong>{track.title}</strong>
                          {track.artist !== selected.artist ? (
                            <div className="muted">{track.artist}</div>
                          ) : null}
                        </div>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => handlePlay(selected.id, track.id)}
                        >
                          Play
                        </button>
                      </li>
                    ))}
                  </ol>
                </>
              ) : (
                <p className="muted">Select a loaded disc to play.</p>
              )}
            </aside>
          </div>
        </>
      )}
    </div>
  )
}
