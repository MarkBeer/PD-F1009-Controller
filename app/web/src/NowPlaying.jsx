import { useCatalog } from './CatalogContext'

export default function NowPlaying() {
  const { now } = useCatalog()

  if (!now) {
    return (
      <footer className="now-playing empty">
        <span>Nothing playing yet — pick a loaded disc or track.</span>
      </footer>
    )
  }

  return (
    <footer className="now-playing">
      {now.cover_url ? (
        <img src={now.cover_url} alt="" className="now-cover" />
      ) : (
        <div className="now-cover placeholder" />
      )}
      <div className="now-meta">
        <div className="now-message">{now.message}</div>
        <div className="now-sub">
          {now.slot != null ? `Slot ${now.slot}` : now.command}
          {now.track_number != null ? ` · Track ${now.track_number}` : ''}
          {now.artist ? ` · ${now.artist}` : ''}
          {now.album_title ? ` — ${now.album_title}` : ''}
        </div>
      </div>
    </footer>
  )
}
