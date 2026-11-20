import { useEffect, useState } from 'react'

/** Cover art with a graceful fallback when the image fails to load. */
export default function CoverImage({
  src,
  alt = '',
  className = '',
  loading = 'lazy',
}) {
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    setFailed(false)
  }, [src])

  if (!src || failed) {
    return (
      <div
        className={['cover-fallback', className].filter(Boolean).join(' ')}
        aria-hidden="true"
      />
    )
  }

  return (
    <img
      src={src}
      alt={alt}
      className={className}
      loading={loading}
      decoding="async"
      onError={() => setFailed(true)}
    />
  )
}
