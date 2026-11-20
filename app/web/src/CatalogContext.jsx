import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react'
import * as api from './api'

const CatalogContext = createContext(null)

export function CatalogProvider({ children }) {
  const [albums, setAlbums] = useState([])
  const [slots, setSlots] = useState([])
  const [library, setLibrary] = useState([])
  const [now, setNow] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [coverBust, setCoverBust] = useState({})

  const refresh = useCallback(async () => {
    setError(null)
    try {
      const [albumData, slotData, libData, nowData] = await Promise.all([
        api.fetchAlbums(),
        api.fetchSlots(),
        api.fetchLibrary(),
        api.fetchNow(),
      ])
      setAlbums(albumData.albums)
      setSlots(slotData.slots)
      setLibrary(libData.discs)
      setNow(nowData.now)
    } catch (err) {
      setError(err.message || String(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const setSlot = useCallback(
    async (slotNumber, discId, { replace = false } = {}) => {
      try {
        await api.assignSlot(slotNumber, discId, { replace })
        await refresh()
        return { ok: true }
      } catch (err) {
        if (err.status === 409) {
          return {
            ok: false,
            conflict: true,
            occupiedBy: err.data?.detail?.disc_id,
            message: err.message,
          }
        }
        throw err
      }
    },
    [refresh],
  )

  const play = useCallback(
    async (discId, trackId = null) => {
      const result = await api.playDisc(discId, trackId)
      const nowData = await api.fetchNow()
      setNow(nowData.now)
      return result
    },
    [],
  )

  const control = useCallback(async (action) => {
    const result = await api.playerControl(action)
    const nowData = await api.fetchNow()
    setNow(nowData.now)
    return result
  }, [])

  const updateAlbum = useCallback(
    async (albumId, fields) => {
      const updated = await api.updateAlbum(albumId, fields)
      await refresh()
      return updated
    },
    [refresh],
  )

  const uploadCover = useCallback(
    async (albumId, file) => {
      const updated = await api.uploadCover(albumId, file)
      setCoverBust((prev) => ({ ...prev, [albumId]: Date.now() }))
      await refresh()
      return updated
    },
    [refresh],
  )

  const importCatalog = useCallback(
    async (file, cropsBase) => {
      const result = await api.importCatalogCsv(file, cropsBase)
      setCoverBust({})
      await refresh()
      return result
    },
    [refresh],
  )

  const coverUrl = useCallback(
    (albumOrUrl, albumId) => {
      const base =
        typeof albumOrUrl === 'string'
          ? albumOrUrl
          : albumOrUrl?.cover_url || `/api/covers/${albumId}`
      const id = albumId ?? albumOrUrl?.id
      const bust = id != null ? coverBust[id] : null
      if (!bust) return base
      return `${base}${base.includes('?') ? '&' : '?'}v=${bust}`
    },
    [coverBust],
  )

  const value = useMemo(
    () => ({
      albums,
      slots,
      library,
      now,
      loading,
      error,
      refresh,
      setSlot,
      play,
      control,
      updateAlbum,
      uploadCover,
      importCatalog,
      coverUrl,
    }),
    [
      albums,
      slots,
      library,
      now,
      loading,
      error,
      refresh,
      setSlot,
      play,
      control,
      updateAlbum,
      uploadCover,
      importCatalog,
      coverUrl,
    ],
  )

  return (
    <CatalogContext.Provider value={value}>{children}</CatalogContext.Provider>
  )
}

export function useCatalog() {
  const ctx = useContext(CatalogContext)
  if (!ctx) {
    throw new Error('useCatalog must be used within CatalogProvider')
  }
  return ctx
}
