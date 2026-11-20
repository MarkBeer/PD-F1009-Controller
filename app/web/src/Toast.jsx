import { useEffect, useState } from 'react'

const SHOW_MS = 5000
const FADE_MS = 400

export default function Toast({ message, onDismiss }) {
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    if (!message) {
      setVisible(false)
      return undefined
    }

    setVisible(true)
    const fadeTimer = window.setTimeout(() => setVisible(false), SHOW_MS)
    const clearTimer = window.setTimeout(() => onDismiss?.(), SHOW_MS + FADE_MS)

    return () => {
      window.clearTimeout(fadeTimer)
      window.clearTimeout(clearTimer)
    }
  }, [message, onDismiss])

  if (!message) return null

  return (
    <div
      className={`toast ${visible ? 'toast-visible' : 'toast-hiding'}`}
      role="status"
      aria-live="polite"
    >
      {message}
    </div>
  )
}
