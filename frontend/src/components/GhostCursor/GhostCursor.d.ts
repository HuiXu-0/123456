import type { CSSProperties, ReactElement } from 'react'

export interface GhostCursorProps {
  className?: string
  style?: CSSProperties
  trailLength?: number
  inertia?: number
  grainIntensity?: number
  bloomStrength?: number
  bloomRadius?: number
  bloomThreshold?: number
  brightness?: number
  color?: string
  mixBlendMode?: string
  edgeIntensity?: number
  maxDevicePixelRatio?: number
  targetPixels?: number
  fadeDelayMs?: number
  fadeDurationMs?: number
  zIndex?: number
}

declare const GhostCursor: (props: GhostCursorProps) => ReactElement
export default GhostCursor
