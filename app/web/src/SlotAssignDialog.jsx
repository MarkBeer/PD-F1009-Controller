import { useEffect } from 'react'
import CoverImage from './CoverImage'

function discLabel(disc) {
  const total = disc.disc_count_in_set || 1
  if (total <= 1) return 'Single disc'
  return `Disc ${disc.disc_number} of ${total}`
}

export default function SlotAssignDialog({
  album,
  coverSrc,
  slotDrafts,
  onDraftChange,
  onApplySlot,
  onClose,
}) {
  useEffect(() => {
    function onKey(e) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  if (!album) return null

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="modal slot-assign-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="slot-assign-title"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="modal-header">
          <h2 id="slot-assign-title">Assign slots</h2>
          <button type="button" className="ghost" onClick={onClose}>
            Close
          </button>
        </header>

        <div className="detail-hero">
          <CoverImage src={coverSrc} alt="" loading="eager" />
          <div>
            <h2>{album.title}</h2>
            <p>{album.artist}</p>
            {album.discogs_release_id ? (
              <p className="muted small">Discogs #{album.discogs_release_id}</p>
            ) : null}
            {album.needs_review ? (
              <span className="badge warn">Needs review</span>
            ) : null}
          </div>
        </div>

        <ul className="disc-list">
          {album.discs.map((disc) => {
            const draft =
              slotDrafts[disc.id] ??
              (disc.slot_number != null ? String(disc.slot_number) : '')
            return (
              <li key={disc.id}>
                <div>
                  <strong>{discLabel(disc)}</strong>
                  <div className="muted">
                    {disc.track_count ?? '?'} tracks ·{' '}
                    {disc.slot_number != null
                      ? `Slot ${disc.slot_number}`
                      : 'Not loaded'}
                  </div>
                </div>
                <form
                  className="slot-form"
                  onSubmit={(e) => {
                    e.preventDefault()
                    onApplySlot(disc, draft)
                  }}
                >
                  <input
                    type="number"
                    min={0}
                    max={300}
                    inputMode="numeric"
                    placeholder="Slot"
                    aria-label={`Slot for ${discLabel(disc)}`}
                    value={draft}
                    onChange={(e) => onDraftChange(disc.id, e.target.value)}
                  />
                  <button type="submit">Set</button>
                  {disc.slot_number != null ? (
                    <button
                      type="button"
                      className="ghost"
                      onClick={() => onApplySlot(disc, '')}
                    >
                      Unload
                    </button>
                  ) : null}
                </form>
              </li>
            )
          })}
        </ul>
      </div>
    </div>
  )
}
