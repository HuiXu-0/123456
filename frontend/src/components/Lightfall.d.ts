import type { CSSProperties, ReactElement } from 'react'

export interface LightfallProps {
  className?: string
  dpr?: number
  paused?: boolean
  colors?: string[]
  backgroundColor?: string
  speed?: number
  streakCount?: number
  streakWidth?: number
  streakLength?: number
  glow?: number
  density?: number
  twinkle?: number
  zoom?: number
  backgroundGlow?: number
  opacity?: number
  mouseInteraction?: boolean
  mouseStrength?: number
  mouseRadius?: number
  mouseDampening?: number
  mixBlendMode?: CSSProperties['mixBlendMode']
}

declare const Lightfall: (props: LightfallProps) => ReactElement
export default Lightfall
