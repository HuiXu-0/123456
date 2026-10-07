import { useId } from 'react'

/**
 * 资源类型插画缩略图（分层圆角 + 柔和色 + 星点的统一风格）。
 *
 * - viewBox 自适应（preserveAspectRatio meet），同一组件既做卡片缩略图也做详情头部。
 * - 每个实例用 useId 派生唯一 filter id，避免卡片缩略图与详情头部同时挂载时
 *   （layoutId 过场期间）出现重复 filter id 导致投影渲染异常。
 */
export type ResourceIllustrationType = 'lecture' | 'video' | 'mindmap' | 'diagram' | 'code' | 'external'

export default function ResourceIllustration({ type }: { type: ResourceIllustrationType }) {
  const uid = useId().replace(/:/g, '')
  const fid = `${uid}-${type}`

  if (type === 'lecture') {
    return (
      <svg className="res-illu" viewBox="0 0 220 170" preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
        <defs>
          <filter id={fid} x="-30%" y="-30%" width="160%" height="160%">
            <feDropShadow dx="0" dy="4" stdDeviation="5" floodColor="#2b6e4f" floodOpacity="0.16" />
          </filter>
        </defs>
        <rect width="220" height="170" fill="#e7f4ee" />
        <g transform="rotate(-6 110 90)" filter={`url(#${fid})`}>
          <rect x="68" y="30" width="84" height="108" rx="10" fill="#fff" />
          <circle cx="86" cy="56" r="6" fill="#3a9d6e" />
          <rect x="100" y="52" width="40" height="7" rx="3.5" fill="#dfe8e2" />
          <circle cx="86" cy="80" r="6" fill="#3a9d6e" />
          <rect x="100" y="76" width="34" height="7" rx="3.5" fill="#dfe8e2" />
          <circle cx="86" cy="104" r="6" fill="#cfe5d8" />
          <rect x="100" y="100" width="40" height="7" rx="3.5" fill="#eef2ef" />
        </g>
        <g transform="rotate(38 150 110)">
          <rect x="142" y="58" width="16" height="74" rx="4" fill="#46b07c" />
          <rect x="142" y="58" width="16" height="20" rx="4" fill="#2f9264" />
          <path d="M142 132 L150 148 L158 132 Z" fill="#f2d9a8" />
          <path d="M150 148 L150 138 L154 138 Z" fill="#2b2d3a" />
        </g>
        <path d="M186 34 Q188 41 195 43 Q188 45 186 52 Q184 45 177 43 Q184 41 186 34Z" fill="#f0b94a" />
      </svg>
    )
  }

  if (type === 'video') {
    return (
      <svg className="res-illu" viewBox="0 0 220 170" preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
        <defs>
          <filter id={fid} x="-30%" y="-30%" width="160%" height="160%">
            <feDropShadow dx="0" dy="4" stdDeviation="5" floodColor="#2d5aa0" floodOpacity="0.18" />
          </filter>
        </defs>
        <rect width="220" height="170" fill="#e8f0fb" />
        <g filter={`url(#${fid})`}>
          <rect x="48" y="40" width="124" height="80" rx="10" fill="#2b3a5c" />
          <rect x="56" y="48" width="108" height="64" rx="6" fill="#3d5180" />
          <circle cx="110" cy="80" r="20" fill="#fff" />
          <path d="M104 70 L122 80 L104 90 Z" fill="#3d5180" />
          <rect x="92" y="120" width="36" height="8" rx="4" fill="#2b3a5c" />
          <rect x="80" y="128" width="60" height="6" rx="3" fill="#b9c6dd" />
        </g>
        <path d="M188 40 Q190 47 197 49 Q190 51 188 58 Q186 51 179 49 Q186 47 188 40Z" fill="#f0b94a" />
      </svg>
    )
  }

  if (type === 'mindmap') {
    return (
      <svg className="res-illu" viewBox="0 0 220 170" preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
        <defs>
          <filter id={fid} x="-30%" y="-30%" width="160%" height="160%">
            <feDropShadow dx="0" dy="3" stdDeviation="4" floodColor="#5b4ba8" floodOpacity="0.18" />
          </filter>
        </defs>
        <rect width="220" height="170" fill="#efeafb" />
        <path d="M96 60 H120 M96 110 H120 M120 60 V110 M120 85 H150" stroke="#b9a9e8" strokeWidth="3" fill="none" />
        <g filter={`url(#${fid})`}>
          <rect x="40" y="46" width="52" height="30" rx="9" fill="#8b7bd6" />
          <rect x="40" y="96" width="52" height="30" rx="9" fill="#a99ae0" />
          <rect x="124" y="70" width="52" height="30" rx="9" fill="#7d6fce" />
          <rect x="150" y="40" width="40" height="24" rx="8" fill="#c3b6ec" />
        </g>
        <circle cx="120" cy="60" r="5" fill="#f0b94a" />
      </svg>
    )
  }

  if (type === 'diagram') {
    return (
      <svg className="res-illu" viewBox="0 0 220 170" preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
        <defs>
          <filter id={fid} x="-30%" y="-30%" width="160%" height="160%">
            <feDropShadow dx="0" dy="3" stdDeviation="4" floodColor="#b5772a" floodOpacity="0.18" />
          </filter>
        </defs>
        <rect width="220" height="170" fill="#fbf2e2" />
        <g filter={`url(#${fid})`}>
          <rect x="42" y="44" width="78" height="82" rx="10" fill="#fff" />
          <rect x="56" y="98" width="12" height="18" rx="3" fill="#8b7bd6" />
          <rect x="74" y="84" width="12" height="32" rx="3" fill="#46b07c" />
          <rect x="92" y="72" width="12" height="44" rx="3" fill="#f0b94a" />
        </g>
        <g filter={`url(#${fid})`} transform="translate(150 92)">
          <circle r="28" fill="#fff" />
          <path d="M0 0 L0 -28 A28 28 0 0 1 24 14 Z" fill="#f0b94a" />
          <path d="M0 0 L24 14 A28 28 0 0 1 -20 19 Z" fill="#46b07c" />
          <circle r="12" fill="#fbf2e2" />
        </g>
        <path d="M196 38 Q197 43 203 44 Q197 46 196 51 Q195 46 189 44 Q195 43 196 38Z" fill="#46b07c" />
      </svg>
    )
  }

  if (type === 'external') {
    // 资源推荐：地球经纬线 + 三个彩色资源节点聚合到中心，喻「AI 联网聚合全球优质资源」
    return (
      <svg className="res-illu" viewBox="0 0 220 170" preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
        <defs>
          <filter id={fid} x="-30%" y="-30%" width="160%" height="160%">
            <feDropShadow dx="0" dy="4" stdDeviation="5" floodColor="#1f7a7a" floodOpacity="0.16" />
          </filter>
        </defs>
        <rect width="220" height="170" fill="#e3f3f3" />
        <line x1="150" y1="58" x2="110" y2="88" stroke="#9bd4ce" strokeWidth="2" />
        <line x1="74" y1="112" x2="110" y2="88" stroke="#9bd4ce" strokeWidth="2" />
        <line x1="158" y1="104" x2="110" y2="88" stroke="#9bd4ce" strokeWidth="2" />
        <g filter={`url(#${fid})`}>
          <circle cx="110" cy="88" r="40" fill="#fff" />
          <circle cx="110" cy="88" r="40" fill="none" stroke="#5bb8b0" strokeWidth="2.5" />
          <ellipse cx="110" cy="88" rx="16" ry="40" fill="none" stroke="#7cc9c2" strokeWidth="2" />
          <line x1="70" y1="88" x2="150" y2="88" stroke="#7cc9c2" strokeWidth="2" />
          <path d="M74 70 Q110 58 146 70 M74 106 Q110 118 146 106" fill="none" stroke="#7cc9c2" strokeWidth="2" />
        </g>
        <g filter={`url(#${fid})`}>
          <circle cx="150" cy="58" r="11" fill="#46b07c" />
          <circle cx="74" cy="112" r="9" fill="#f0b94a" />
          <circle cx="158" cy="104" r="8" fill="#8b7bd6" />
        </g>
        <path d="M188 40 Q190 47 197 49 Q190 51 188 58 Q186 51 179 49 Q186 47 188 40Z" fill="#f0b94a" />
      </svg>
    )
  }

  // code
  return (
    <svg className="res-illu" viewBox="0 0 220 170" preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <defs>
        <filter id={fid} x="-30%" y="-30%" width="160%" height="160%">
          <feDropShadow dx="0" dy="4" stdDeviation="5" floodColor="#1f2233" floodOpacity="0.22" />
        </filter>
      </defs>
      <rect width="220" height="170" fill="#eceef3" />
      <g filter={`url(#${fid})`}>
        <rect x="40" y="42" width="132" height="86" rx="10" fill="#2b2f42" />
        <circle cx="54" cy="56" r="4" fill="#ec6a5e" />
        <circle cx="68" cy="56" r="4" fill="#f0bd4a" />
        <circle cx="82" cy="56" r="4" fill="#46b07c" />
        <rect x="54" y="72" width="40" height="6" rx="3" fill="#5b6b8f" />
        <rect x="54" y="86" width="64" height="6" rx="3" fill="#46556f" />
        <rect x="54" y="100" width="30" height="6" rx="3" fill="#46556f" />
        <path d="M128 76 L118 90 L128 104 M150 76 L160 90 L150 104 M140 72 L138 108" stroke="#9fe0c0" strokeWidth="3.5" fill="none" strokeLinecap="round" />
      </g>
      <g transform="translate(160 110)" filter={`url(#${fid})`}>
        <circle r="18" fill="#46b07c" />
        <circle r="7" fill="#eceef3" />
      </g>
    </svg>
  )
}
