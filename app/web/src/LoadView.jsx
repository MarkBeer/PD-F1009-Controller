import { useCallback, useEffect, useMemo, useState } from 'react'
import AlbumEditDialog from './AlbumEditDialog'
import CatalogImportDialog from './CatalogImportDialog'
import CoverImage from './CoverImage'
import SelectPlayerDialog from './SelectPlayerDialog'
import SlotAssignDialog from './SlotAssignDialog'
import { useCatalog } from './CatalogContext'
import {
  fetchArduinoSettings,
  fetchCatalogDefaults,
  saveArduinoSettings,
  testArduinoSettings,
} from './api'
import Toast from './Toast'

function discLabel(disc) {
  const total = disc.disc_count_in_set || 1
  if (total <= 1) return 'Single disc'
  return `Disc ${disc.disc_number} of ${total}`
}

function compareText(a, b) {
  return a.localeCompare(b, undefined, { sensitivity: 'base' })
}

function sortAlbums(list, sortBy) {
  const items = [...list]
  switch (sortBy) {
    case 'artist':
      return items.sort(
        (a, b) =>
          compareText(a.artist, b.artist) ||
          compareText(a.title, b.title) ||
          a.photo_index - b.photo_index,
      )
    case 'album':
      return items.sort(
        (a, b) =>
          compareText(a.title, b.title) ||
          compareText(a.artist, b.artist) ||
          a.photo_index - b.photo_index,
      )
    case 'loaded':
      return items.sort((a, b) => {
        const aLoaded = a.discs.some((d) => d.slot_number != null) ? 0 : 1
        const bLoaded = b.discs.some((d) => d.slot_number != null) ? 0 : 1
        return (
          aLoaded - bLoaded ||
          compareText(a.artist, b.artist) ||
          compareText(a.title, b.title)
        )
      })
    case 'catalogue':
    default:
      // Scan / physical shelf order: photo batch then index in that photo.
      return items.sort(
        (a, b) =>
          compareText(a.source_image || '', b.source_image || '') ||
          a.photo_index - b.photo_index,
      )
  }
}

export default function LoadView() {
  const {
    albums,
    slots,
    setSlot,
    loading,
    error,
    updateAlbum,
    syncFromDiscogs,
    uploadCover,
    importCatalog,
    coverUrl,
  } = useCatalog()
  const [query, setQuery] = useState('')
  const [sortBy, setSortBy] = useState('catalogue')
  const [loadedFilter, setLoadedFilter] = useState('all')
  const [selectedId, setSelectedId] = useState(null)
  const [slotDrafts, setSlotDrafts] = useState({})
  const [status, setStatus] = useState('')
  const [editingAlbum, setEditingAlbum] = useState(null)
  const [importing, setImporting] = useState(false)
  const [showImport, setShowImport] = useState(false)
  const [showPlayer, setShowPlayer] = useState(false)
  const [defaultCropsBase, setDefaultCropsBase] = useState('')
  const [arduinoUrl, setArduinoUrl] = useState('')
  const [arduinoBusy, setArduinoBusy] = useState(false)
  const dismissStatus = useCallback(() => setStatus(''), [])

  useEffect(() => {
    let cancelled = false
    Promise.all([fetchCatalogDefaults(), fetchArduinoSettings()])
      .then(([defaults, arduino]) => {
        if (cancelled) return
        setDefaultCropsBase(defaults.crops_base || '')
        setArduinoUrl(arduino.base_url || '')
      })
      .catch(() => {
        if (!cancelled) {
          setDefaultCropsBase('')
          setArduinoUrl('')
        }
      })
    return () => {
      cancelled = true
    }
  }, [])

  async function handleSaveArduino(url) {
    setArduinoBusy(true)
    try {
      const saved = await saveArduinoSettings(url)
      setArduinoUrl(saved.base_url || '')
      setStatus(
        saved.base_url
          ? `Arduino URL saved: ${saved.base_url}`
          : 'Arduino URL cleared (local play mode)',
      )
      return saved
    } finally {
      setArduinoBusy(false)
    }
  }

  async function handleTestArduino(url) {
    setArduinoBusy(true)
    try {
      // Persist draft URL first so the health check uses it.
      const saved = await saveArduinoSettings(url)
      setArduinoUrl(saved.base_url || '')
      const result = await testArduinoSettings()
      setStatus(
        result.ok
          ? `Arduino reachable at ${result.base_url}`
          : result.detail || 'Arduino test failed',
      )
      return result
    } finally {
      setArduinoBusy(false)
    }
  }

  async function handleCatalogImport(file, cropsBase) {
    setImporting(true)
    try {
      const result = await importCatalog(file, cropsBase)
      const stats = result.stats || {}
      setStatus(
        `Imported ${stats.albums ?? 0} albums, ${stats.discs ?? 0} discs, ${stats.tracks ?? 0} tracks` +
          (stats.covers != null ? ` (${stats.covers} covers)` : ''),
      )
      setSelectedId(null)
      setSlotDrafts({})
      setDefaultCropsBase(cropsBase)
    } finally {
      setImporting(false)
    }
  }

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    const matched = albums.filter((album) => {
      const loadedCount = album.discs.filter((d) => d.slot_number != null).length
      if (loadedFilter === 'loaded' && loadedCount === 0) return false
      if (loadedFilter === 'unloaded' && loadedCount > 0) return false
      if (!q) return true
      if (album.artist.toLowerCase().includes(q)) return true
      if (album.title.toLowerCase().includes(q)) return true
      return album.discs.some((d) => String(d.slot_number ?? '').includes(q))
    })
    return sortAlbums(matched, sortBy)
  }, [albums, query, sortBy, loadedFilter])

  const selected = albums.find((a) => a.id === selectedId) || null

  const occupiedLookup = useMemo(() => {
    const map = new Map()
    for (const album of albums) {
      for (const disc of album.discs) {
        map.set(disc.id, {
          album,
          disc,
          label: `${album.artist} — ${album.title} (${discLabel(disc)})`,
        })
      }
    }
    return map
  }, [albums])

  const freeSlots = useMemo(() => {
    // Prefer rack 1–300; single loader (0) only after the rack is full.
    const empty = slots
      .filter((s) => s.disc_id == null)
      .map((s) => s.slot_number)
      .sort((a, b) => {
        if (a === 0) return 1
        if (b === 0) return -1
        return a - b
      })
    return empty
  }, [slots])

  function selectAlbum(albumId) {
    setSelectedId(albumId)
    const album = albums.find((a) => a.id === albumId)
    if (!album) return

    const unloaded = album.discs.filter((d) => d.slot_number == null)
    if (unloaded.length === 0) return

    setSlotDrafts((prev) => {
      const next = { ...prev }
      unloaded.forEach((disc, i) => {
        const suggestion = freeSlots[i]
        if (suggestion != null) {
          next[disc.id] = String(suggestion)
        }
      })
      return next
    })
  }

  async function applySlot(disc, rawValue) {
    const text = String(rawValue).trim()
    if (text === '') {
      if (disc.slot_number == null) return
      await setSlot(disc.slot_number, null)
      setStatus(`Unloaded ${discLabel(disc)}`)
      return
    }
    const slotNumber = Number(text)
    if (!Number.isInteger(slotNumber) || slotNumber < 0 || slotNumber > 300) {
      setStatus('Slot must be an integer from 0 to 300 (0 = single loader)')
      return
    }
    if (disc.slot_number === slotNumber) return

    let result = await setSlot(slotNumber, disc.id, { replace: false })
    if (!result.ok && result.conflict) {
      const other = occupiedLookup.get(result.occupiedBy)
      const otherLabel = other ? other.label : `disc #${result.occupiedBy}`
      const ok = window.confirm(
        `Slot ${slotNumber} already holds ${otherLabel}. Replace it?`,
      )
      if (!ok) {
        setStatus('Assignment cancelled')
        return
      }
      result = await setSlot(slotNumber, disc.id, { replace: true })
    }
    if (result.ok) {
      setStatus(`Loaded into slot ${slotNumber}`)
      setSlotDrafts((prev) => {
        const next = { ...prev }
        delete next[disc.id]
        return next
      })
    }
  }

  async function clearSlot(slotNumber) {
    await setSlot(slotNumber, null)
    setStatus(`Cleared slot ${slotNumber}`)
  }

  if (loading) {
    return <div className="page-status">Loading catalog…</div>
  }
  if (error) {
    return <div className="page-status error">{error}</div>
  }

  return (
    <div className="load-view">
      <header className="view-header">
        <div>
          <h1>Load CDs</h1>
          <p className="muted">
            Assign catalog discs to PD-F1009 slots 1–300, or 0 for the single
            loader.
          </p>
        </div>
        <div className="header-controls">
          <label className="search">
            <span className="sr-only">Search catalog</span>
            <input
              type="search"
              placeholder="Search artist, album…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              disabled={importing}
            />
          </label>
          <label className="sort-control">
            <span className="sr-only">Filter by loaded</span>
            <select
              value={loadedFilter}
              onChange={(e) => setLoadedFilter(e.target.value)}
              aria-label="Filter by loaded"
              disabled={importing}
            >
              <option value="all">All albums</option>
              <option value="loaded">Loaded</option>
              <option value="unloaded">Not loaded</option>
            </select>
          </label>
          <label className="sort-control">
            <span className="sr-only">Sort albums</span>
            <select
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value)}
              aria-label="Sort albums"
              disabled={importing}
            >
              <option value="catalogue">Catalogue order</option>
              <option value="artist">Artist A–Z</option>
              <option value="album">Album A–Z</option>
              <option value="loaded">Loaded first</option>
            </select>
          </label>
          <button
            type="button"
            className="csv-upload-btn"
            disabled={importing}
            onClick={() => setShowImport(true)}
          >
            {importing ? 'Importing…' : 'Upload CSV'}
          </button>
          <button
            type="button"
            className="csv-upload-btn"
            disabled={arduinoBusy}
            onClick={() => setShowPlayer(true)}
            title={arduinoUrl || 'Local play mode'}
          >
            Select player
          </button>
        </div>
      </header>

      <Toast message={status} onDismiss={dismissStatus} />

      {albums.length === 0 ? (
        <div className="empty-state">
          <p>No catalog loaded yet.</p>
          <p className="muted">
            Upload a CD index CSV and set the folder that contains the cover
            crops (or the project root those paths are relative to).
          </p>
          <button
            type="button"
            className="primary"
            disabled={importing}
            onClick={() => setShowImport(true)}
          >
            {importing ? 'Importing…' : 'Upload CSV'}
          </button>
        </div>
      ) : null}

      <section className="cover-grid" aria-label="Catalog">
        {filtered.map((album) => {
          const loaded = album.discs.filter((d) => d.slot_number != null).length
          return (
            <div
              key={album.id}
              className={`cover-card ${selectedId === album.id ? 'selected' : ''}`}
              role="button"
              tabIndex={0}
              onClick={() => selectAlbum(album.id)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  selectAlbum(album.id)
                }
              }}
            >
              <div className="cover-card-media">
                <CoverImage
                  src={album.has_cover !== false ? coverUrl(album) : null}
                  alt=""
                />
                {loaded > 0 ? (
                  <span
                    className="cover-loaded-tick"
                    title={`${loaded} of ${album.discs.length} loaded`}
                    aria-label={`${loaded} of ${album.discs.length} discs loaded`}
                  >
                    ✓
                  </span>
                ) : null}
              </div>
              <div className="cover-meta">
                <strong>{album.title}</strong>
                <span>{album.artist}</span>
                <span className="badge">
                  {album.discs.length} disc
                  {album.discs.length === 1 ? '' : 's'}
                </span>
                {loaded > 0 ? (
                  <span className="badge badge-loaded">
                    {loaded} loaded
                  </span>
                ) : null}
              </div>
              <button
                type="button"
                className="card-menu-trigger"
                aria-label={`Edit ${album.title}`}
                onClick={(e) => {
                  e.stopPropagation()
                  setEditingAlbum(album)
                }}
              >
                ···
              </button>
            </div>
          )
        })}
        {filtered.length === 0 && albums.length > 0 ? (
          <p className="muted">No albums match that search or filter.</p>
        ) : null}
      </section>

      <section className="rack" aria-label="Changer rack">
        <div className="rack-header">
          <h2>Rack</h2>
          <span className="muted">
            {slots.filter((s) => s.disc_id).length} of 301 occupied
          </span>
        </div>
        <div className="rack-grid">
          {slots.map((slot) => (
            <button
              key={slot.slot_number}
              type="button"
              title={
                slot.disc_id
                  ? `Slot ${slot.slot_number}: ${slot.artist} — ${slot.album_title}`
                  : `Slot ${slot.slot_number} empty`
              }
              className={`rack-cell ${slot.disc_id ? 'filled' : ''} ${
                slot.slot_number === 0 ? 'single' : ''
              }`}
              onClick={() => {
                if (slot.disc_id) {
                  const ok = window.confirm(
                    `Clear slot ${slot.slot_number}?`,
                  )
                  if (ok) clearSlot(slot.slot_number)
                }
              }}
            >
              {slot.cover_url ? (
                <CoverImage
                  src={coverUrl(slot.cover_url, slot.album_id)}
                  alt=""
                  loading="eager"
                />
              ) : (
                <span>{slot.slot_number}</span>
              )}
            </button>
          ))}
        </div>
      </section>

      {selected ? (
        <SlotAssignDialog
          album={selected}
          coverSrc={
            selected.has_cover !== false ? coverUrl(selected) : null
          }
          slotDrafts={slotDrafts}
          onDraftChange={(discId, value) =>
            setSlotDrafts((prev) => ({
              ...prev,
              [discId]: value,
            }))
          }
          onApplySlot={applySlot}
          onClose={() => setSelectedId(null)}
        />
      ) : null}

      {editingAlbum ? (
        <AlbumEditDialog
          album={editingAlbum}
          onClose={() => setEditingAlbum(null)}
          onSave={async (id, fields) => {
            await updateAlbum(id, fields)
            setStatus('Album details saved')
          }}
          onSyncDiscogs={async (id, options) => {
            const result = await syncFromDiscogs(id, options)
            const sync = result?.sync
            const parts = [
              sync?.artist && sync?.title
                ? `${sync.artist} — ${sync.title}`
                : 'Discogs metadata',
              sync?.track_count != null ? `${sync.track_count} tracks` : null,
              sync?.cover_updated ? 'cover updated' : 'cover unchanged',
            ].filter(Boolean)
            setStatus(`Fetched from Discogs: ${parts.join(' · ')}`)
            return result
          }}
          onUploadCover={async (id, file) => {
            await uploadCover(id, file)
            setStatus('Cover image updated')
          }}
        />
      ) : null}

      {showImport ? (
        <CatalogImportDialog
          defaultCropsBase={defaultCropsBase}
          busy={importing}
          onClose={() => {
            if (!importing) setShowImport(false)
          }}
          onImport={handleCatalogImport}
        />
      ) : null}

      {showPlayer ? (
        <SelectPlayerDialog
          initialUrl={arduinoUrl}
          busy={arduinoBusy}
          onClose={() => {
            if (!arduinoBusy) setShowPlayer(false)
          }}
          onSave={handleSaveArduino}
          onTest={handleTestArduino}
        />
      ) : null}
    </div>
  )
}
