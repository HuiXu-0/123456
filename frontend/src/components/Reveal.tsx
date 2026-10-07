import { motion, type Variants } from 'framer-motion'
import type { ReactNode } from 'react'

/**
 * 统一的卡片入场动画：opacity 0→1 + 轻微上移(16px→0)，0.4s ease-out，
 * 同组多卡 stagger 70ms 逐个出现；挂载/页面切换即触发。
 * 三大主页面的内容卡片统一接入，保证动效完全一致。
 */
export const revealContainer: Variants = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.07 } },
}

export const revealItem: Variants = {
  hidden: { opacity: 0, y: 16 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.4, ease: 'easeOut' } },
  /* 供 AnimatePresence 场景（如分步表单）复用；普通列表不触发 */
  exit: { opacity: 0, y: -12, transition: { duration: 0.25, ease: 'easeIn' } },
}

interface RevealProps {
  children: ReactNode
  className?: string
}

/** 卡片组容器：负责逐个错峰（staggerChildren）*/
export function RevealGroup({ children, className }: RevealProps) {
  return (
    <motion.div className={className} variants={revealContainer} initial="hidden" animate="visible">
      {children}
    </motion.div>
  )
}

/** 单张卡片：淡入 + 上移。需作为 RevealGroup 的子元素以获得错峰 */
export function RevealItem({ children, className }: RevealProps) {
  return (
    <motion.div className={className} variants={revealItem}>
      {children}
    </motion.div>
  )
}
