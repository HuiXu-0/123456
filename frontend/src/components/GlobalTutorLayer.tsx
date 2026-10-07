import { useEffect, useState } from 'react'
import AskTutorDock from './AskTutorDock'
import SelectionAskBubble from './SelectionAskBubble'
import ResourceModal from './ResourceModal'
import { getTutorContext, onTutorContext, onResourceGenerated, type TutorContext, type ResourceGeneratedPayload } from '../services/tutorBus'

/**
 * 全局即时辅导层（B-2 全局化）：把「选中即问」气泡 + 常驻辅导 dock 提升到 App 顶层，一次接入、
 * 全站正文内容区可用（有序学习 / 文档学习等）。当前辅导上下文由各内容页 setTutorContext 声明，
 * 这里订阅并透传给复用的 AskTutorDock —— 不改 dock、不逐页重复接选中监听。
 */
export default function GlobalTutorLayer() {
  const [ctx, setCtx] = useState<TutorContext>(() => getTutorContext())
  useEffect(() => {
    // 再同步一次：内容页的 setTutorContext 可能在本层订阅前就已广播（挂载期竞态），
    // 订阅后立刻补读一次当前上下文，避免停留在通用兜底。
    setCtx(getTutorContext())
    return onTutorContext(setCtx)
  }, [])

  /* 资源生成完成 → 弹出资源展示弹窗 */
  const [modalOpen, setModalOpen] = useState(false)
  const [modalData, setModalData] = useState<ResourceGeneratedPayload | null>(null)

  useEffect(() => {
    return onResourceGenerated((payload) => {
      setModalData(payload)
      setModalOpen(true)
    })
  }, [])

  return (
    <>
      {/* 全局模式：不传 containerRef，正文内容区（.markdown-body / [data-ask-content]）通用触发 */}
      <SelectionAskBubble />
      <AskTutorDock kpId={ctx.kpId} kpName={ctx.kpName} />

      {/* 资源展示弹窗 */}
      <ResourceModal
        open={modalOpen}
        resources={modalData?.resources ?? []}
        kpName={modalData?.kpName ?? ctx.kpName}
        problemPoint={modalData?.problemPoint ?? ''}
        onClose={() => setModalOpen(false)}
      />
    </>
  )
}
