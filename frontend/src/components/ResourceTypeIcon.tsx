import { useId } from 'react'
import './ResourceTypeIcon.css'

/**
 * 资源类型图标（全站统一）· 磨砂玻璃圆角方块 + 柔和渐变 + 白色主体图形。
 *
 * 七类学习资源各一枚精致图标，替代原先各页零散、风格不一的 emoji（📖🎬📊🧠💻📝🃏 及其变体）。
 * 纯 SVG 绘制（渐变 + 透明度叠层模拟磨砂玻璃质感），无外部图片、无位图依赖，任意尺寸清晰。
 * 主题色内建（蓝/紫/青绿/粉/靛蓝/橙/品紫），底色自带故深浅主题一致好看；外层阴影/描边
 * 走设计令牌，暗色下磨砂玻璃质感同样成立。
 *
 * 消费方：我的资源库卡片/筛选/小结、文档学习右栏生成按钮、学习资源速览生成清单、
 * Agent 工作流成果、学习流输入 Tab —— 统一 `<ResourceTypeIcon kind size />`。
 */
export type ResourceIconKind =
  | 'lecture'
  | 'video'
  | 'diagram'
  | 'mindmap'
  | 'code'
  | 'quiz'
  | 'flashcard'

/** 每类主题色 [浅, 深] 对角渐变（清新柔和；与需求指定色系一一对应）。 */
const GRAD: Record<ResourceIconKind, [string, string]> = {
  lecture: ['#5b9dff', '#2f6fe0'], // 蓝
  video: ['#a97bf5', '#6d3fd4'], // 紫
  diagram: ['#37d6bf', '#12a597'], // 青绿
  mindmap: ['#ff92c2', '#ef5aa0'], // 粉
  code: ['#6f7cf2', '#3f49cf'], // 靛蓝
  quiz: ['#ffb35c', '#f4842f'], // 橙
  flashcard: ['#b98cf6', '#8348e0'], // 品紫（与视频紫区分）
}

/** 白色主体图形（在 48×48 磨砂玻璃底上，描边 + 半透明填充叠层，深浅主题皆清晰）。 */
function Glyph({ kind }: { kind: ResourceIconKind }) {
  const stroke = {
    stroke: '#fff',
    strokeWidth: 2,
    strokeLinejoin: 'round' as const,
    strokeLinecap: 'round' as const,
    fill: 'none',
  }
  switch (kind) {
    case 'lecture': // 带书签的文档
      return (
        <g className="rti__glyph">
          <rect x={15} y={13} width={16} height={22} rx={2.6} {...stroke} fill="rgba(255,255,255,.16)" />
          <path d="M27 13v10l-2-1.7-2 1.7V13z" fill="#fff" />
          <line x1={18.5} y1={26} x2={27.5} y2={26} {...stroke} strokeWidth={1.8} />
          <line x1={18.5} y1={30} x2={27.5} y2={30} {...stroke} strokeWidth={1.8} />
        </g>
      )
    case 'video': // 胶片 + 播放键
      return (
        <g className="rti__glyph">
          <rect x={13} y={15} width={22} height={18} rx={3} {...stroke} fill="rgba(255,255,255,.16)" />
          <line x1={13} y1={20} x2={35} y2={20} {...stroke} strokeWidth={1.4} opacity={0.85} />
          <line x1={13} y1={28} x2={35} y2={28} {...stroke} strokeWidth={1.4} opacity={0.85} />
          <path d="M22 21.5v5l4.5-2.5z" fill="#fff" />
        </g>
      )
    case 'diagram': // 立体六边形（前后错位叠出体积）
      return (
        <g className="rti__glyph">
          <path d="M25.5 15.5l6 3.4v6.8l-6 3.4-6-3.4v-6.8z" fill="rgba(255,255,255,.28)" />
          <path d="M23 14l6 3.4v6.8L23 27.6l-6-3.4v-6.8z" {...stroke} fill="rgba(255,255,255,.18)" />
        </g>
      )
    case 'mindmap': // 节点连线树
      return (
        <g className="rti__glyph">
          <path d="M18 24h5m0-6l5-3m-5 9l5 0m-5 6l5 3" {...stroke} strokeWidth={1.8} />
          <circle cx={16} cy={24} r={3.4} fill="#fff" />
          <circle cx={31} cy={15} r={2.8} fill="#fff" />
          <circle cx={32} cy={24} r={2.8} fill="#fff" />
          <circle cx={31} cy={33} r={2.8} fill="#fff" />
        </g>
      )
    case 'code': // 代码窗口 </>
      return (
        <g className="rti__glyph">
          <rect x={13} y={15} width={22} height={18} rx={3} {...stroke} fill="rgba(255,255,255,.16)" />
          <line x1={13} y1={20} x2={35} y2={20} {...stroke} strokeWidth={1.4} opacity={0.85} />
          <circle cx={16.5} cy={17.5} r={0.9} fill="#fff" />
          <circle cx={19.3} cy={17.5} r={0.9} fill="#fff" />
          <path d="M22 23l-3 3.5 3 3.5m6-7l3 3.5-3 3.5" {...stroke} strokeWidth={1.8} />
        </g>
      )
    case 'quiz': // 清单 + 笔
      return (
        <g className="rti__glyph">
          <rect x={13.5} y={13} width={16} height={22} rx={2.6} {...stroke} fill="rgba(255,255,255,.16)" />
          <path d="M17.5 20l1.6 1.6 2.4-2.6M17.5 27l1.6 1.6 2.4-2.6" {...stroke} strokeWidth={1.8} />
          <line x1={24} y1={19.5} x2={26.5} y2={19.5} {...stroke} strokeWidth={1.6} />
          <line x1={24} y1={26.5} x2={26.5} y2={26.5} {...stroke} strokeWidth={1.6} />
          <path d="M31.5 27.5l3.4 3.4-4.6 1.2 1.2-4.6z" fill="#fff" />
          <path d="M33 26l2 2" {...stroke} strokeWidth={1.8} />
        </g>
      )
    case 'flashcard': // 叠层卡片 + 星
      return (
        <g className="rti__glyph">
          <rect x={17} y={15} width={17} height={13} rx={2.4} transform="rotate(-9 25 21)" fill="rgba(255,255,255,.34)" />
          <rect x={14} y={20} width={18} height={13} rx={2.4} {...stroke} fill="rgba(255,255,255,.18)" />
          <path d="M23 24.2l1.15 2.33 2.57.37-1.86 1.81.44 2.56-2.3-1.2-2.3 1.2.44-2.56-1.86-1.81 2.57-.37z" fill="#fff" />
        </g>
      )
  }
}

export default function ResourceTypeIcon({
  kind,
  size = 40,
  className = '',
  title,
}: {
  kind: ResourceIconKind
  size?: number
  className?: string
  title?: string
}) {
  const uid = useId().replace(/:/g, '')
  const [light, dark] = GRAD[kind]
  const gid = `rti-g-${kind}-${uid}`
  const sid = `rti-s-${uid}`
  const hid = `rti-h-${uid}`
  return (
    <svg
      className={`rti rti--${kind} ${className}`.trim()}
      width={size}
      height={size}
      viewBox="0 0 48 48"
      role="img"
      aria-label={title ?? kind}
    >
      {title ? <title>{title}</title> : null}
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor={light} />
          <stop offset="1" stopColor={dark} />
        </linearGradient>
        {/* 顶部磨砂高光：白→透明纵向 */}
        <linearGradient id={sid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#fff" stopOpacity="0.34" />
          <stop offset="0.55" stopColor="#fff" stopOpacity="0" />
        </linearGradient>
        {/* 左上角柔光：径向 */}
        <radialGradient id={hid} cx="0.28" cy="0.24" r="0.7">
          <stop offset="0" stopColor="#fff" stopOpacity="0.4" />
          <stop offset="1" stopColor="#fff" stopOpacity="0" />
        </radialGradient>
      </defs>
      {/* 磨砂玻璃底：主题渐变 + 顶部高光 + 角部柔光 + 内描边 */}
      <rect x="2" y="2" width="44" height="44" rx="13" fill={`url(#${gid})`} />
      <rect x="2" y="2" width="44" height="44" rx="13" fill={`url(#${hid})`} />
      <rect x="2" y="2" width="44" height="44" rx="13" fill={`url(#${sid})`} />
      <rect
        x="2.75"
        y="2.75"
        width="42.5"
        height="42.5"
        rx="12.25"
        fill="none"
        stroke="#fff"
        strokeOpacity="0.4"
        strokeWidth="1"
      />
      <Glyph kind={kind} />
    </svg>
  )
}
