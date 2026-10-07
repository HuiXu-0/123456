import { lazy, Suspense } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import MarkdownRenderer from './MarkdownRenderer'
import type { ResourceGeneratedPayload } from '../services/tutorBus'
import './ResourceModal.css'

const MermaidDiagram = lazy(() => import('./MermaidDiagram'))
const VideoLecture = lazy(() => import('./VideoLecture'))

const TYPE_ICON: Record<string, string> = {
  diagram: '🗺️',
  example: '📝',
  video: '🎬',
  lecture: '📖',
  external: '🔗',
}

const EXTERNAL_TYPE_ICON: Record<string, string> = {
  '视频': '🎬',
  '论文': '📄',
  '文档': '📘',
  '课程': '🎓',
}

interface Props {
  open: boolean
  resources: ResourceGeneratedPayload['resources']
  kpName: string
  problemPoint: string
  onClose: () => void
}

/**
 * 即时辅导·资源展示弹窗（问题 5 / 问题 6 改造）。
 *
 * 生成的资源内容从侧边栏移到屏幕中央弹窗展示，提供更宽敞的浏览体验。
 * 支持滚动查看多个资源，点击遮罩或 X 按钮关闭。
 */
export default function ResourceModal({ open, resources, kpName, problemPoint, onClose }: Props) {
  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="rm-mask"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={onClose}
        >
          <motion.div
            className="rm"
            role="dialog"
            aria-modal="true"
            aria-label="辅导资源"
            initial={{ opacity: 0, y: 24, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 24, scale: 0.97 }}
            transition={{ type: 'spring', stiffness: 240, damping: 24 }}
            onClick={(e) => e.stopPropagation()}
          >
            <button className="rm__close" aria-label="关闭" onClick={onClose}>
              ×
            </button>

            <div className="rm__head">
              <span className="rm__badge">💡 即时辅导·资源详情</span>
              <h3 className="rm__title">
                「{kpName}」的针对性资源
              </h3>
              <p className="rm__sub">
                问题点：{problemPoint}
              </p>
            </div>

            <div className="rm__body">
              {resources.map((resource, idx) => (
                <div key={idx} className="rm__item">
                  <ResourceContent resource={resource} />
                </div>
              ))}
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}

/** 单个资源内容渲染 */
function ResourceContent({ resource }: { resource: ResourceGeneratedPayload['resources'][number] }) {
  return (
    <div className="rm__resource">
      <div className="rm__resource-head">
        <span className="rm__resource-icon">{TYPE_ICON[resource.type] ?? '📄'}</span>
        <span className="rm__resource-title">{resource.title}</span>
      </div>

      <div className="rm__resource-body">
        {/* 图解 */}
        {resource.type === 'diagram' && resource.mermaid && (
          <Suspense fallback={<div className="rm__loading">图解渲染中…</div>}>
            <MermaidDiagram chart={resource.mermaid} />
          </Suspense>
        )}

        {/* 讲义 */}
        {resource.type === 'lecture' && resource.markdown && (
          <div className="rm__md">
            <MarkdownRenderer content={resource.markdown} />
          </div>
        )}

        {/* 例题 */}
        {resource.type === 'example' && (
          <div className="rm__example">
            <p className="rm__example-q">{resource.statement}</p>
            {resource.solution && (
              <div className="rm__example-solution">
                <strong>解析：</strong>
                <pre className="rm__example-a">{resource.solution}</pre>
              </div>
            )}
          </div>
        )}

        {/* 视频 */}
        {resource.type === 'video' && resource.scenes && (
          <Suspense fallback={<div className="rm__loading">讲解视频渲染中…</div>}>
            <VideoLecture
              title={resource.title}
              scenes={resource.scenes.map((sc) => ({ title: sc.title, points: sc.points, narration: sc.narration }))}
            />
          </Suspense>
        )}

        {/* 外部资源推荐 */}
        {resource.type === 'external' && resource.items && (
          <div className="rm__external">
            {resource.items.length === 0 ? (
              <p className="rm__empty">暂无推荐资源。</p>
            ) : (
              <ul className="rm__rec-list">
                {resource.items.map((r) => (
                  <li key={r.id} className="rm__rec">
                    <div className="rm__rec-main">
                      <span className="rm__rec-type">{EXTERNAL_TYPE_ICON[r.type] ?? '📄'} {r.type}</span>
                      <span className="rm__rec-title">{r.title}</span>
                      <span className="rm__rec-source">{r.source}{r.duration ? ` · ${r.duration}` : ''}</span>
                      <span className="rm__rec-reason">💡 {r.reason}</span>
                    </div>
                    <a className="rm__rec-open" href={r.url} target="_blank" rel="noreferrer">
                      ↗ 打开
                    </a>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
