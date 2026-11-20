async function request(path, options = {}) {
  const headers = { ...(options.headers || {}) }
  const isForm = typeof FormData !== 'undefined' && options.body instanceof FormData
  if (!isForm && options.body != null && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json'
  }
  const res = await fetch(path, {
    ...options,
    headers,
  })
  let data = null
  const text = await res.text()
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = { detail: text }
    }
  }
  if (!res.ok) {
    const err = new Error(
      typeof data?.detail === 'string'
        ? data.detail
        : data?.detail?.message || res.statusText || 'Request failed',
    )
    err.status = res.status
    err.data = data
    throw err
  }
  return data
}

export function fetchAlbums(q = '') {
  const qs = q ? `?q=${encodeURIComponent(q)}` : ''
  return request(`/api/albums${qs}`)
}

export function fetchAlbum(id) {
  return request(`/api/albums/${id}`)
}

export function updateAlbum(id, fields) {
  return request(`/api/albums/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(fields),
  })
}

export function uploadCover(id, file) {
  const body = new FormData()
  body.append('file', file)
  return request(`/api/covers/${id}`, {
    method: 'PUT',
    body,
  })
}

export function fetchCatalogDefaults() {
  return request('/api/catalog/defaults')
}

export function importCatalogCsv(file, cropsBase) {
  const body = new FormData()
  body.append('file', file)
  body.append('crops_base', cropsBase)
  return request('/api/catalog/import', {
    method: 'POST',
    body,
  })
}

export function fetchSlots() {
  return request('/api/slots')
}

export function assignSlot(slotNumber, discId, { replace = false } = {}) {
  return request(`/api/slots/${slotNumber}`, {
    method: 'PUT',
    body: JSON.stringify({ disc_id: discId, replace }),
  })
}

export function fetchLibrary(q = '') {
  const qs = q ? `?q=${encodeURIComponent(q)}` : ''
  return request(`/api/player/library${qs}`)
}

export function playDisc(discId, trackId = null) {
  return request('/api/player/play', {
    method: 'POST',
    body: JSON.stringify({ disc_id: discId, track_id: trackId }),
  })
}

export function playerControl(action) {
  return request('/api/player/control', {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
}

export function fetchNow() {
  return request('/api/player/now')
}

export function fetchArduinoSettings() {
  return request('/api/settings/arduino')
}

export function saveArduinoSettings(baseUrl) {
  return request('/api/settings/arduino', {
    method: 'PUT',
    body: JSON.stringify({ base_url: baseUrl }),
  })
}

export function testArduinoSettings() {
  return request('/api/settings/arduino/test')
}

export function fetchHealth() {
  return request('/api/health')
}
