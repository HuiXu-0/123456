import './GlassDevice.css'

export default function GlassDevice() {
  return (
    <div className="glass-device">
      {/* 底座光环 */}
      <div className="glass-device__base">
        <div className="glass-device__base-ring" />
        <div className="glass-device__base-glow" />
      </div>

      {/* 3D 玻璃立方体 */}
      <div className="glass-device__scene">
        <div className="glass-device__cube">
          {/* 6 个面 - 磨砂玻璃质感 */}
          <div className="glass-device__face glass-device__face--front">
            <div className="glass-device__face-highlight" />
          </div>
          <div className="glass-device__face glass-device__face--back" />
          <div className="glass-device__face glass-device__face--right">
            <div className="glass-device__face-highlight" />
          </div>
          <div className="glass-device__face glass-device__face--left" />
          <div className="glass-device__face glass-device__face--top">
            <div className="glass-device__face-highlight" />
          </div>
          <div className="glass-device__face glass-device__face--bottom" />

          {/* 内部发光核心 */}
          <div className="glass-device__core" />
        </div>
      </div>

      {/* 粒子光点 */}
      <div className="glass-device__particles">
        <span className="glass-device__particle glass-device__particle--1" />
        <span className="glass-device__particle glass-device__particle--2" />
        <span className="glass-device__particle glass-device__particle--3" />
        <span className="glass-device__particle glass-device__particle--4" />
        <span className="glass-device__particle glass-device__particle--5" />
        <span className="glass-device__particle glass-device__particle--6" />
      </div>

      {/* 顶部光束 */}
      <div className="glass-device__beam" />
    </div>
  )
}
