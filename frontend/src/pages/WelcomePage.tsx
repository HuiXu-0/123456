import { useEffect, useRef, useState } from 'react'
import './WelcomePage.css'

interface WelcomePageProps {
  onEnter: () => void
}

/** 演示视频路径（放入 frontend/public/demo.mp4 后「观看演示」按钮自动出现） */
const DEMO_VIDEO_SRC = '/demo.mp4'

/* ============================================================
   落地页演示数据（固定常量）——未登录展示用，非真实用户数据，
   与后端无关。视觉与文案以 docs/welcome-knowledge-core.html 为准。
   ============================================================ */

/** 知识核心 6 大主域（颜色 + 名称），围绕中心枢纽分布 */
const DEMO_DOMAINS: Array<[color: string, name: string]> = [
  ['#6f9280', '机器学习'],
  ['#2c3e42', '深度学习'],
  ['#b5824e', '大模型'],
  ['#8bab97', '视觉'],
  ['#a3799a', '扩散'],
  ['#5b8bb0', '智能体'],
]

/** 底部「主动发现」轮播文案 */
const DEMO_INSIGHTS = [
  '我发现你在逻辑推理上很有优势，深度学习会学得很快。',
  '你的数学基础扎实，但神经网络部分我们可以走慢一点。',
  '按你的节奏，我为你规划了一条约两周的学习路径。',
]

interface KNode {
  x: number
  y: number
  r: number
  c: string
  main: boolean
}

interface KEdge {
  a: { x: number; y: number }
  b: { x: number; y: number }
  el: SVGLineElement | null
  flow: boolean
}

/**
 * 未登录欢迎页（AI Knowledge Core 版）。
 * 初始化动画时序与原型一致：扫描 → 连线生成 → 节点点亮 → 枢纽浮现 →
 * READY → 主动发现轮播；进入视口（threshold .3）才播放，StrictMode 下
 * effect 双执行由 cleanup 完整复位（清空 SVG/日志/定时器）保证幂等。
 */
export default function WelcomePage({ onEnter }: WelcomePageProps) {
  const rootRef = useRef<HTMLDivElement>(null)
  const glowRef = useRef<HTMLDivElement>(null)
  const shellRef = useRef<HTMLDivElement>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  const canvasRef = useRef<HTMLDivElement>(null)
  const scanRef = useRef<HTMLDivElement>(null)
  const hubRef = useRef<HTMLDivElement>(null)
  const logLinesRef = useRef<HTMLDivElement>(null)
  const logStateRef = useRef<HTMLSpanElement>(null)
  const insightRef = useRef<HTMLDivElement>(null)
  const insightTextRef = useRef<HTMLSpanElement>(null)

  /* ===== 演示视频弹层 ===== */
  const [demoReady, setDemoReady] = useState(false)
  const [demoOpen, setDemoOpen] = useState(false)

  // 探测 public/demo.mp4 是否就绪：未就绪时按钮不渲染，避免"点了没反应"。
  // Range 只取 1 字节防止拉全量视频；Accept: text/html 让 dev/生产的 SPA
  // 回退返回 index.html（200）而非 404，控制台保持 0 报错，再按
  // content-type 区分真视频与回退页。
  useEffect(() => {
    let cancelled = false
    fetch(DEMO_VIDEO_SRC, { headers: { Accept: 'text/html', Range: 'bytes=0-0' } })
      .then((res) => {
        const type = res.headers.get('content-type') ?? ''
        res.body?.cancel().catch(() => {})
        if (!cancelled) setDemoReady(res.ok && !type.includes('text/html'))
      })
      .catch(() => {
        if (!cancelled) setDemoReady(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  // 弹层打开时：ESC 关闭 + 锁定页面滚动
  useEffect(() => {
    if (!demoOpen) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setDemoOpen(false)
    }
    window.addEventListener('keydown', onKey)
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = prevOverflow
    }
  }, [demoOpen])

  /* ===== AI Knowledge Core 初始化序列（等效移植原型原生 JS） ===== */
  useEffect(() => {
    const svg = svgRef.current
    const scan = scanRef.current
    const hub = hubRef.current
    const logLines = logLinesRef.current
    const logState = logStateRef.current
    const insight = insightRef.current
    const insightText = insightTextRef.current
    const shell = shellRef.current
    if (!svg || !scan || !hub || !logLines || !logState || !insight || !insightText || !shell) return

    const NS = 'http://www.w3.org/2000/svg'
    const W = 460
    const H = 360
    const cx = W / 2
    const cy = H / 2

    // 生成节点位置（围绕中心分布）：6 个主节点 + 22 个次级散点
    const nodes: KNode[] = []
    DEMO_DOMAINS.forEach((d, i) => {
      const a = (i / 6) * 6.2832 - 1.57
      nodes.push({ x: cx + Math.cos(a) * 105, y: cy + Math.sin(a) * 90, r: 6, c: d[0], main: true })
    })
    for (let i = 0; i < 22; i++) {
      const main = nodes[i % 6]
      const a = Math.random() * 6.28
      const dist = 28 + Math.random() * 40
      let x = main.x + Math.cos(a) * dist
      let y = main.y + Math.sin(a) * dist
      x = Math.max(24, Math.min(W - 24, x))
      y = Math.max(20, Math.min(H - 20, y))
      nodes.push({ x, y, r: 2.5 + Math.random() * 2, c: main.c, main: false })
    }

    // 预建边（不显示）：主节点连中心，散点连最近主节点
    const edges: KEdge[] = []
    nodes.forEach((n) => {
      if (n.main) {
        edges.push({ a: { x: cx, y: cy }, b: n, el: null, flow: true })
      } else {
        let best = 0
        let bd = 1e9
        nodes.forEach((m, j) => {
          if (!m.main) return
          const dd = (n.x - m.x) ** 2 + (n.y - m.y) ** 2
          if (dd < bd) {
            bd = dd
            best = j
          }
        })
        edges.push({ a: nodes[best], b: n, el: null, flow: Math.random() > 0.5 })
      }
    })

    // 定时器统一登记，cleanup 一次性清掉
    const timers: number[] = []
    let insightInterval: number | undefined
    const later = (fn: () => void, ms: number) => {
      timers.push(window.setTimeout(fn, ms))
    }

    function addLog(text: string, sub: string, active: boolean) {
      const l = document.createElement('div')
      l.className = 'klog-line'
      l.innerHTML = `<span class="ck">${active ? '◍' : '✓'}</span><span class="lt">${text}${sub ? `<small>${sub}</small>` : ''}</span>`
      if (active) l.classList.add('active')
      logLines!.appendChild(l)
      requestAnimationFrame(() => l.classList.add('show'))
      return l
    }
    function drawEdges() {
      edges.forEach((e) => {
        const ln = document.createElementNS(NS, 'line') as SVGLineElement
        ln.setAttribute('x1', String(e.a.x))
        ln.setAttribute('y1', String(e.a.y))
        ln.setAttribute('x2', String(e.b.x))
        ln.setAttribute('y2', String(e.b.y))
        ln.setAttribute('class', 'kedge' + (e.flow ? ' flow' : ''))
        svg!.appendChild(ln)
        e.el = ln
      })
    }
    function drawNodes() {
      const els: SVGGElement[] = []
      nodes.forEach((n) => {
        const g = document.createElementNS(NS, 'g') as SVGGElement
        g.setAttribute('class', 'knode')
        const c = document.createElementNS(NS, 'circle')
        c.setAttribute('cx', String(n.x))
        c.setAttribute('cy', String(n.y))
        c.setAttribute('r', String(n.r))
        c.setAttribute('fill', n.c)
        c.setAttribute('opacity', n.main ? '1' : '.8')
        if (n.main) {
          c.setAttribute('stroke', '#f5f1e8')
          c.setAttribute('stroke-width', '1.5')
        }
        g.appendChild(c)
        svg!.appendChild(g)
        els.push(g)
      })
      // 逐个点亮，主节点附加呼吸动画
      els.forEach((g, i) =>
        later(() => {
          g.classList.add('on')
          if (nodes[i].main) g.classList.add('breathe')
        }, i * 45)
      )
    }

    // ===== 时序编排（与原型各 Phase 时间点一致）=====
    function run() {
      // Phase 1: 扫描
      later(() => {
        logState!.textContent = 'SCANNING'
        scan!.classList.add('run')
        addLog('分析学习历史', 'READING PROFILE…', true)
      }, 400)
      // Phase 2: 生成边
      later(() => {
        drawEdges()
        edges.forEach((e, i) => later(() => e.el?.classList.add('on'), i * 20))
      }, 1600)
      // Phase 3: 识别能力（点亮节点）
      later(() => {
        logState!.textContent = 'BUILDING'
        logLines!.querySelector('.active')?.classList.remove('active')
        const ck = logLines!.querySelector('.ck')
        if (ck) ck.textContent = '✓'
        addLog('识别能力结构', 'MAPPING NODES…', true)
        drawNodes()
      }, 1900)
      // Phase 4: 生成路径
      later(() => {
        logLines!.querySelectorAll('.active').forEach((l) => {
          l.classList.remove('active')
          const ck = l.querySelector('.ck')
          if (ck) ck.textContent = '✓'
        })
        addLog('生成学习路径', 'PATH READY', true)
        hub!.classList.add('on')
      }, 3400)
      // Phase 5: 完成
      later(() => {
        logState!.textContent = 'READY'
        logLines!.querySelectorAll('.active').forEach((l) => {
          l.classList.remove('active')
          const ck = l.querySelector('.ck')
          if (ck) ck.textContent = '✓'
        })
      }, 4600)
      // Phase 6: 主动发现（轮播）
      let ii = 0
      later(() => {
        insight!.classList.add('show')
        const cycle = () => {
          insightText!.textContent = DEMO_INSIGHTS[ii]
          ii = (ii + 1) % DEMO_INSIGHTS.length
        }
        cycle()
        insightInterval = window.setInterval(() => {
          insight!.classList.remove('show')
          later(() => {
            cycle()
            insight!.classList.add('show')
          }, 500)
        }, 5200)
      }, 5000)
    }

    // 进入视口才启动
    const io = new IntersectionObserver(
      (es) =>
        es.forEach((e) => {
          if (e.isIntersecting) {
            run()
            io.disconnect()
          }
        }),
      { threshold: 0.3 }
    )
    io.observe(shell)

    return () => {
      io.disconnect()
      timers.forEach((t) => clearTimeout(t))
      if (insightInterval !== undefined) clearInterval(insightInterval)
      // 完整复位，保证 StrictMode 双执行 / 重挂载时从零重播
      svg.innerHTML = ''
      logLines.innerHTML = ''
      logState.textContent = 'INITIALIZING'
      hub.classList.remove('on')
      insight.classList.remove('show')
      scan.classList.remove('run')
    }
  }, [])

  /* ===== 滚动 reveal + 鼠标光晕 + 能力矩阵卡片聚光 ===== */
  useEffect(() => {
    const root = rootRef.current
    const cg = glowRef.current
    if (!root || !cg) return

    const io = new IntersectionObserver(
      (es) =>
        es.forEach((e) => {
          if (e.isIntersecting) {
            e.target.classList.add('in')
            io.unobserve(e.target)
          }
        }),
      { threshold: 0.1 }
    )
    root.querySelectorAll('.reveal').forEach((el) => io.observe(el))

    const onMove = (e: MouseEvent) => {
      cg.style.opacity = '1'
      cg.style.left = e.clientX + 'px'
      cg.style.top = e.clientY + 'px'
    }
    const onLeave = () => {
      cg.style.opacity = '0'
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseleave', onLeave)

    const cells = Array.from(root.querySelectorAll<HTMLElement>('.cell'))
    const cellHandlers = cells.map((c) => {
      const h = (e: MouseEvent) => {
        const r = c.getBoundingClientRect()
        c.style.setProperty('--mx', e.clientX - r.left + 'px')
        c.style.setProperty('--my', e.clientY - r.top + 'px')
      }
      c.addEventListener('mousemove', h)
      return [c, h] as const
    })

    return () => {
      io.disconnect()
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseleave', onLeave)
      cellHandlers.forEach(([c, h]) => c.removeEventListener('mousemove', h))
    }
  }, [])

  return (
    <div className="wk" ref={rootRef}>
      <div className="bg-fx">
        <div className="grid" />
        <div className="glow g1" />
        <div className="glow g2" />
        <div className="glow g3" />
      </div>
      <div className="cursor-glow" ref={glowRef} />

      <nav>
        <div className="nav-in">
          <div className="brand">
            <img className="logo" src="/logo/logo-mark.png" alt="智学中枢" />
            <div className="brand-text">
              智学中枢<small>AI LEARNING PLATFORM</small>
            </div>
          </div>
          <div className="navlinks">
            <a href="#matrix">能力矩阵</a>
            <a href="#personalize">个性化</a>
            <a href="#doc">文档学习</a>
            <a href="#method">学习方法</a>
          </div>
          <a
            className="nav-cta"
            href="#"
            onClick={(e) => {
              e.preventDefault()
              onEnter()
            }}
          >
            进入平台
          </a>
        </div>
      </nav>

      <header className="hero">
        <div className="wrap">
          <div className="pill">
            <span className="dot" />
            多智能体驱动 · 防幻觉可溯源 · 懂科学学习方法
          </div>
          <h1>
            你的 AI 学习管家
            <br />
            <span className="grad">读懂你，因材施教</span>
          </h1>
          <p className="sub">
            不只是生成学习资源，而是一位懂科学学习方法、能读懂你、会规划、卡住能辅导、说话有依据的 AI 学习伙伴。
          </p>
          <div className="cta-row">
            <button className="btn btn-primary" onClick={onEnter}>
              开始个性化学习
            </button>
            {demoReady && (
              <button className="btn btn-ghost" onClick={() => setDemoOpen(true)}>
                ▶ 观看演示
              </button>
            )}
          </div>

          <div className="kcore-shell" ref={shellRef}>
            <div className="winbar">
              <i />
              <i />
              <i />
              <span className="wintitle">AI KNOWLEDGE CORE · 学情管家</span>
            </div>
            <div className="kcore-stage">
              {/* 左:AI 分析过程日志 */}
              <div className="kcore-log">
                <div className="klog-title">
                  <span className="kdot" />
                  <span ref={logStateRef}>INITIALIZING</span>
                </div>
                <div className="klog-lines" ref={logLinesRef} />
              </div>
              {/* 中:知识核心画布 */}
              <div className="kcore-canvas" ref={canvasRef}>
                <svg className="ksvg" viewBox="0 0 460 400" ref={svgRef} />
                <div className="kscan" ref={scanRef} />
                <div className="kcore-hub" ref={hubRef}>
                  <div className="khub-glyph" />
                </div>
                {/* 悬浮信息卡(鼠标靠近展开)——演示数据 */}
                <div className="kfloat kf1">
                  <span className="kf-k">学习画像</span>
                  <b>机器学习 · 中级</b>
                </div>
                <div className="kfloat kf2">
                  <span className="kf-k">知识缺口</span>
                  <b>神经网络 · 待补强</b>
                </div>
                <div className="kfloat kf3">
                  <span className="kf-k">推荐路径</span>
                  <b>深度学习 · 已生成</b>
                </div>
              </div>
            </div>
            {/* 底部:主动发现的惊喜 */}
            <div className="kcore-insight" ref={insightRef}>
              <span className="ki-spark">✦</span>
              <span ref={insightTextRef} />
            </div>
          </div>
        </div>
      </header>

      <section className="section" id="matrix">
        <div className="wrap">
          <div className="sec-head reveal">
            <div className="kicker">AI Capability Matrix</div>
            <h2>
              一套 AI 能力矩阵，
              <br />
              覆盖学习全流程
            </h2>
            <p>五大创新维度 · 从理解你，到为你规划、生成、辅导、评估的完整闭环</p>
          </div>
          <div className="matrix">
            <div className="cell c-3 r-2 reveal">
              <div className="cell-head">
                <div className="cell-ico">
                  <svg viewBox="0 0 24 24">
                    <circle cx="12" cy="8" r="4" />
                    <path d="M4 21c0-4 4-6 8-6s8 2 8 6" />
                  </svg>
                </div>
                <div className="cell-head-txt">
                  <span className="num-badge">01 · 个性化</span>
                  <h3>行为实测画像 · 双驱动路径</h3>
                </div>
              </div>
              <p>用做题反推真实能力（而非自我报告），能力定"学什么"、偏好定"怎么学"，配学习时间线——真因材施教。</p>
              <div className="flow">
                <b>
                  <svg viewBox="0 0 24 24">
                    <path d="M21 12a8 8 0 01-8 8H5l-2 2V6a3 3 0 013-3h8a8 8 0 017 9z" />
                  </svg>
                  对话
                </b>
                <em>→</em>
                <b>
                  <svg viewBox="0 0 24 24">
                    <path d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2" />
                    <rect x="9" y="3" width="6" height="4" rx="1" />
                    <path d="M9 14l2 2 4-4" />
                  </svg>
                  诊断微测
                </b>
                <em>→</em>
                <b>
                  <svg viewBox="0 0 24 24">
                    <path d="M4 8h8M18 8h2M4 16h2M12 16h8" />
                    <circle cx="15" cy="8" r="2.5" />
                    <circle cx="9" cy="16" r="2.5" />
                  </svg>
                  偏好
                </b>
                <em>→</em>
                <b>
                  <svg viewBox="0 0 24 24">
                    <circle cx="6" cy="19" r="2" />
                    <circle cx="18" cy="5" r="2" />
                    <path d="M8 19h8a4 4 0 000-8H8a4 4 0 010-8h8" />
                  </svg>
                  专属路径
                </b>
              </div>
              <div className="foot">
                <span>三段式画像</span>
                <span>能力/偏好双驱动</span>
                <span>学习时间线</span>
              </div>
            </div>
            <div className="cell c-3 r-2 reveal d1">
              <div className="cell-head">
                <div className="cell-ico">
                  <svg viewBox="0 0 24 24">
                    <circle cx="6" cy="6" r="2.5" />
                    <circle cx="18" cy="6" r="2.5" />
                    <circle cx="12" cy="18" r="2.5" />
                    <path d="M6 8.5v3a2 2 0 002 2h8a2 2 0 002-2v-3M12 13.5v2" />
                  </svg>
                </div>
                <div className="cell-head-txt">
                  <span className="num-badge">02 · 技术架构</span>
                  <h3>真实多智能体协同</h3>
                </div>
              </div>
              <p>基于 LangGraph 的五节点工作流——诊断→检索→生成→审核→决策，各专家 Agent 分工协作、真实编排。平台自身即多智能体最佳实践。</p>
              <div className="flow flow-lg">
                <span className="flow-node">
                  <i className="fn-ico">
                    <svg viewBox="0 0 24 24">
                      <path d="M3 12h4l3-8 4 16 3-8h4" />
                    </svg>
                  </i>
                  <span>诊断</span>
                </span>
                <em>→</em>
                <span className="flow-node">
                  <i className="fn-ico">
                    <svg viewBox="0 0 24 24">
                      <circle cx="11" cy="11" r="6" />
                      <path d="M20 20l-4.5-4.5" />
                    </svg>
                  </i>
                  <span>检索</span>
                </span>
                <em>→</em>
                <span className="flow-node">
                  <i className="fn-ico">
                    <svg viewBox="0 0 24 24">
                      <path d="M12 4l1.8 4.6 4.2 1.8-4.2 1.8L12 17l-1.8-4.8L6 10.4l4.2-1.8L12 4z" />
                      <path d="M19 15l.9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9L19 15z" />
                    </svg>
                  </i>
                  <span>生成</span>
                </span>
                <em>→</em>
                <span className="flow-node">
                  <i className="fn-ico">
                    <svg viewBox="0 0 24 24">
                      <path d="M12 3l7 3v5c0 5-3 8-7 10-4-2-7-5-7-10V6l7-3z" />
                      <path d="M9 12l2 2 4-4" />
                    </svg>
                  </i>
                  <span>审核</span>
                </span>
                <em>→</em>
                <span className="flow-node">
                  <i className="fn-ico">
                    <svg viewBox="0 0 24 24">
                      <circle cx="6" cy="6" r="2.5" />
                      <circle cx="6" cy="18" r="2.5" />
                      <circle cx="18" cy="6" r="2.5" />
                      <path d="M6 8.5v7M18 8.5a9 9 0 01-9 9" />
                    </svg>
                  </i>
                  <span>决策</span>
                </span>
              </div>
              <div className="foot">
                <span>五层架构</span>
                <span>完整RAG管线</span>
                <span>多模型·魔搭</span>
              </div>
            </div>
            <div className="cell c-2 reveal">
              <div className="cell-head">
                <div className="cell-ico">
                  <svg viewBox="0 0 24 24">
                    <path d="M12 3L2 8l10 5 10-5-10-5zM4 10v6c0 1 3.5 3 8 3s8-2 8-3v-6" />
                  </svg>
                </div>
                <div className="cell-head-txt">
                  <span className="num-badge">03</span>
                  <h3>科学学习方法论</h3>
                </div>
              </div>
              <p>苏格拉底/费曼/康奈尔/掌握式，教育方法论工程化。</p>
            </div>
            <div className="cell c-2 reveal d1">
              <div className="cell-head">
                <div className="cell-ico">
                  <svg viewBox="0 0 24 24">
                    <path d="M12 3l7 3v5c0 5-3 8-7 10-4-2-7-5-7-10V6l7-3z" />
                    <path d="M9 12l2 2 4-4" />
                  </svg>
                </div>
                <div className="cell-head-txt">
                  <span className="num-badge">04</span>
                  <h3>可信防幻觉</h3>
                </div>
              </div>
              <p>逐句接地、真算幻觉率、来源可溯源，幻觉率 &lt;5%。</p>
            </div>
            <div className="cell c-2 reveal d2">
              <div className="cell-head">
                <div className="cell-ico">
                  <svg viewBox="0 0 24 24">
                    <rect x="3" y="3" width="7" height="7" rx="2" />
                    <rect x="14" y="3" width="7" height="7" rx="2" />
                    <rect x="8.5" y="14" width="7" height="7" rx="2" />
                  </svg>
                </div>
                <div className="cell-head-txt">
                  <span className="num-badge">05</span>
                  <h3>78 点知识图谱</h3>
                </div>
              </div>
              <p>7 大板块带先修依赖，AI 按需生成零手工成本。</p>
            </div>
            <div className="cell c-4 reveal">
              <div className="cell-head">
                <div className="cell-ico">
                  <svg viewBox="0 0 24 24">
                    <path d="M6 3h9l4 4v14a1 1 0 01-1 1H6a1 1 0 01-1-1V4a1 1 0 011-1z" />
                    <path d="M14 3v5h5M8 13h8M8 17h5" />
                  </svg>
                </div>
                <div className="cell-head-txt">
                  <span className="num-badge">06 · 对标 NotebookLM</span>
                  <h3>文档学习 · 基于你自己的资料</h3>
                </div>
              </div>
              <p>上传任意资料 → 与文档即问即答（基于文档、标注出处）→ 一键生成六类多模态学习资源。突破预置知识库局限。</p>
              <div className="foot">
                <span>多文件上传</span>
                <span>文档问答·溯源</span>
                <span>多模态生成</span>
              </div>
            </div>
            <div className="cell c-2 reveal d1">
              <div className="cell-head">
                <div className="cell-ico">
                  <svg viewBox="0 0 24 24">
                    <path d="M21 12a8 8 0 01-8 8H5l-2 2V6a3 3 0 013-3h8a8 8 0 017 9z" />
                  </svg>
                </div>
                <div className="cell-head-txt">
                  <span className="num-badge">07</span>
                  <h3>苏格拉底即时辅导</h3>
                </div>
              </div>
              <p>卡住就能问、选中即问，追问引导你想通。</p>
            </div>
          </div>
        </div>
      </section>

      <section className="section" id="personalize" style={{ paddingTop: 0 }}>
        <div className="wrap">
          <div className="feature reveal">
            <div className="ftext">
              <div className="kicker">创新 02 · 真因材施教</div>
              <h3>
                不是"千人一面"，
                <br />
                而是"一人一路径"
              </h3>
              <p className="lead">用做题反推真实能力，而非"你觉得自己几分"。基础不同的人，路径节点、顺序、每步的学习形式，都不一样。</p>
              <ul>
                <li>能力画像 → 决定学什么、什么顺序</li>
                <li>偏好画像 → 决定每步用什么形式</li>
                <li>学习时间线 → 时长/周期/进度/节奏</li>
              </ul>
            </div>
            <div className="shot">
              <div className="winbar">
                <i />
                <i />
                <i />
              </div>
              <img
                className="shot-img"
                src="/welcome-shots/path-timeline.png"
                alt="个性化学习路径页实拍：路径节点时间线、学习里程碑与能力/偏好双驱动规划"
                loading="lazy"
              />
            </div>
          </div>
          <div className="feature rev reveal" id="doc">
            <div className="ftext">
              <div className="kicker">创新 05 · 对标 NotebookLM</div>
              <h3>基于你自己的资料学习</h3>
              <p className="lead">上传论文、课件、任意资料——与文档即问即答，一键生成讲义、图解、闪卡等多模态学习资源。</p>
              <ul>
                <li>多文件上传 · 文档专属知识隔离</li>
                <li>与文档对话 · 流式即问即答 · 溯源可查</li>
                <li>一键生成六类学习资源</li>
              </ul>
            </div>
            <div className="shot">
              <div className="winbar">
                <i />
                <i />
                <i />
              </div>
              <img
                className="shot-img"
                src="/welcome-shots/doc-learning.png"
                alt="文档学习页实拍：来源与概览、文档即问即答（标注出处）、六类资源生成三栏工作台"
                loading="lazy"
              />
            </div>
          </div>
          <div className="feature reveal" id="method">
            <div className="ftext">
              <div className="kicker">创新 01 · 科学学习方法论</div>
              <h3>让学习"科学地发生"</h3>
              <p className="lead">输入 → 康奈尔笔记 → 费曼讲解 → 代码实操 → 阶段测试，科学编排的有序学习流程，覆盖完整认知链条。</p>
              <ul>
                <li>苏格拉底式启发 · 不直接给答案</li>
                <li>费曼讲解 · 以教代学暴露盲区</li>
                <li>掌握式学习 · 达标才进阶（70 分）</li>
              </ul>
            </div>
            <div className="shot">
              <div className="winbar">
                <i />
                <i />
                <i />
              </div>
              <img
                className="shot-img"
                src="/welcome-shots/learning-flow.png"
                alt="有序学习流实拍：输入→康奈尔笔记→费曼讲解→代码实操阶段步进器与定制讲义正文"
                loading="lazy"
              />
            </div>
          </div>
        </div>
      </section>

      <section className="final">
        <div className="wrap">
          <h2 className="reveal">
            让每一个想学 AI 的人，
            <br />
            <span className="grad">都有一位懂你的 AI 学习管家</span>
          </h2>
          <p className="reveal d1">无论你是求职、考研、科研、转行，还是纯粹热爱 —— 从这里开始。</p>
          <button className="btn btn-primary reveal d2" style={{ padding: '16px 42px', fontSize: 16 }} onClick={onEnter}>
            立即开始
          </button>
        </div>
      </section>

      <footer>
        <div className="wrap">智学中枢 · AI Learning Platform ｜ LearnPilot 2026</div>
      </footer>

      {demoOpen && (
        <div className="demo-modal" role="dialog" aria-modal="true" aria-label="产品演示视频" onClick={() => setDemoOpen(false)}>
          <div className="demo-player" onClick={(e) => e.stopPropagation()}>
            <button className="demo-close" aria-label="关闭演示视频" onClick={() => setDemoOpen(false)}>
              ✕
            </button>
            <video src={DEMO_VIDEO_SRC} controls autoPlay playsInline />
          </div>
        </div>
      )}
    </div>
  )
}
