import './IsometricCube.css'

export default function IsometricCube() {
  return (
    <div className="iso-cube-container">
      {/* Floating particles around the cube */}
      <div className="iso-cube-particles">
        <span className="iso-particle iso-particle--1" />
        <span className="iso-particle iso-particle--2" />
        <span className="iso-particle iso-particle--3" />
        <span className="iso-particle iso-particle--4" />
        <span className="iso-particle iso-particle--5" />
      </div>

      {/* The 3D isometric cube */}
      <div className="iso-cube-scene">
        <div className="iso-cube">
          <div className="iso-cube__face iso-cube__face--front" />
          <div className="iso-cube__face iso-cube__face--back" />
          <div className="iso-cube__face iso-cube__face--right" />
          <div className="iso-cube__face iso-cube__face--left" />
          <div className="iso-cube__face iso-cube__face--top" />
          <div className="iso-cube__face iso-cube__face--bottom" />
        </div>
      </div>

      {/* Glow ring beneath */}
      <div className="iso-cube-glow" />
    </div>
  )
}
