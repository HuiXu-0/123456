import { useEffect, useRef } from 'react'
import * as THREE from 'three'

/* 着色器（来自 particle-vortex-effect，效果模式 3 = 漩涡；逐字保留）*/
const vertexShader = "uniform float uTime; uniform float uMorph; uniform float uPointSize; uniform int uEffectMode; uniform float uEffectIntensity; uniform float uExplosionTime; uniform float uCanvasLightness; uniform float uParticleColorMode; uniform vec3 uCustomParticleColor; attribute vec3 targetPosition; attribute vec3 targetColor; attribute vec3 color; attribute vec3 randomOffset; varying vec3 vColor; varying float vDistance; void main() { vec3 mixedBase = mix(color, targetColor, uMorph); vColor = mix(mixedBase, uCustomParticleColor, uParticleColorMode); vec3 pos = mix(position, targetPosition, uMorph); float effectMix = uEffectIntensity; float angle = atan(pos.z, pos.x); float radius = length(pos.xz); float height = pos.y; float spiralSpeed = uTime * 2.0 + height * 0.3; float newAngle = angle + spiralSpeed * effectMix; float vortexPull = (1.0 - abs(height) / 20.0) * effectMix; float newRadius = radius * (1.0 - vortexPull * 0.5) + sin(uTime * 3.0 + height) * effectMix; float lift = effectMix * 5.0 * (1.0 - radius / 20.0); pos.x = cos(newAngle) * newRadius; pos.z = sin(newAngle) * newRadius; pos.y = height + lift * sin(uTime + radius); vec4 mvPosition = modelViewMatrix * vec4(pos, 1.0); float dist = length(pos); vDistance = dist; gl_PointSize = (uPointSize / -mvPosition.z) * (1.2 + sin(uTime * 3.0 + dist * 0.15) * 0.5); gl_Position = projectionMatrix * mvPosition; }"

const fragmentShader = "uniform float uTime; uniform float uCanvasLightness; varying vec3 vColor; varying float vDistance; void main() { float dist = distance(gl_PointCoord, vec2(0.5)); if (dist > 0.5) discard; float strength = pow(1.0 - dist * 2.0, 1.6); vec3 finalColor = vColor * 2.0; float alpha = strength * (0.8 + sin(vDistance * 0.3 + uTime) * 0.2); gl_FragColor = vec4(finalColor, alpha); }"

interface ParticleVortexProps {
  /** 粒子数（默认 55000，兼顾观感与性能）*/
  count?: number
  className?: string
  /** 主色 / 点缀色 / 浅色点（hex），默认青绿+暖金，随主题传入 */
  primary?: string
  accent?: string
  light?: string
}

export default function ParticleVortex({
  count = 55000,
  className,
  primary = '#6FB3A6',
  accent = '#E0A458',
  light = '#CBE3DC',
}: ParticleVortexProps) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const container = ref.current
    if (!container) return

    let raf = 0
    let disposed = false
    const w = () => container.clientWidth || window.innerWidth
    const h = () => container.clientHeight || window.innerHeight

    const scene = new THREE.Scene()
    const camera = new THREE.PerspectiveCamera(35, w() / h(), 0.1, 1000)
    camera.position.z = 45

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' })
    renderer.setSize(w(), h())
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.setClearColor(0x000000, 0) // 透明，叠加在落地页深色背景之上
    container.appendChild(renderer.domElement)

    // 几何：程序化漩涡列（无图片 morph）
    const geometry = new THREE.BufferGeometry()
    const positions = new Float32Array(count * 3)
    const targetPositions = new Float32Array(count * 3)
    const colors = new Float32Array(count * 3)
    const targetColors = new Float32Array(count * 3)
    const randomOffsets = new Float32Array(count * 3)

    // 主题配色：青绿(主) + 暖金(点缀) + 浅色点（随 props / 主题切换，无紫色）
    const cPrimary = new THREE.Color(primary)
    const cAccent = new THREE.Color(accent)
    const cLight = new THREE.Color(light)

    for (let i = 0; i < count; i++) {
      const i3 = i * 3
      const t = (Math.random() - 0.5) * 5.0
      const angle = Math.random() * Math.PI * 2
      const radiusBase = 0.4 + Math.pow(Math.abs(t), 2.4)
      const radius = radiusBase * (0.75 + Math.random() * 0.55)
      const x = radius * Math.cos(angle) * 2.9
      const z = radius * Math.sin(angle) * 2.9
      const y = t * 7.5
      positions[i3] = x; positions[i3 + 1] = y; positions[i3 + 2] = z
      targetPositions[i3] = x; targetPositions[i3 + 1] = y; targetPositions[i3 + 2] = z
      randomOffsets[i3] = (Math.random() - 0.5) * 2
      randomOffsets[i3 + 1] = (Math.random() - 0.5) * 2
      randomOffsets[i3 + 2] = (Math.random() - 0.5) * 2
      const r = Math.random()
      const c = r > 0.7 ? cAccent : r > 0.32 ? cPrimary : cLight
      colors[i3] = c.r; colors[i3 + 1] = c.g; colors[i3 + 2] = c.b
      targetColors[i3] = c.r; targetColors[i3 + 1] = c.g; targetColors[i3 + 2] = c.b
    }
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3))
    geometry.setAttribute('targetPosition', new THREE.BufferAttribute(targetPositions, 3))
    geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3))
    geometry.setAttribute('targetColor', new THREE.BufferAttribute(targetColors, 3))
    geometry.setAttribute('randomOffset', new THREE.BufferAttribute(randomOffsets, 3))

    const material = new THREE.ShaderMaterial({
      vertexShader,
      fragmentShader,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      uniforms: {
        uTime: { value: 0 },
        uMorph: { value: 0 },
        uPointSize: { value: 150 },
        uEffectMode: { value: 3 },
        uEffectIntensity: { value: 1 },
        uExplosionTime: { value: 0 },
        uCanvasLightness: { value: 0 },
        uParticleColorMode: { value: 0 },
        uCustomParticleColor: { value: new THREE.Vector3(0.4, 0.6, 1) },
      },
    })

    const points = new THREE.Points(geometry, material)
    scene.add(points)

    let time = 0
    const animate = () => {
      if (disposed) return
      raf = requestAnimationFrame(animate)
      time += 0.008
      points.rotation.y += 0.008 * 1.5
      points.rotation.z += 0.001
      points.rotation.x = Math.sin(time * 0.15) * 0.12
      material.uniforms.uTime.value = time
      renderer.render(scene, camera)
    }
    animate()

    const onResize = () => {
      if (disposed) return
      camera.aspect = w() / h()
      camera.updateProjectionMatrix()
      renderer.setSize(w(), h())
    }
    window.addEventListener('resize', onResize)

    // 清理：避免 WebGL 上下文泄漏（落地页卸载时）
    return () => {
      disposed = true
      cancelAnimationFrame(raf)
      window.removeEventListener('resize', onResize)
      geometry.dispose()
      material.dispose()
      renderer.dispose()
      if (renderer.domElement.parentNode === container) container.removeChild(renderer.domElement)
    }
  }, [count, primary, accent, light])

  return <div ref={ref} className={className} aria-hidden="true" />
}
