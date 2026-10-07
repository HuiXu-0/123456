/* 学习路径课程数据（共享数据源）：
   学习路径页与首页引导卡共用，避免重复定义。 */

export type LessonStatus = 'completed' | 'in_progress' | 'pending'

export interface Lesson {
  sequence: number
  topic: string
  difficulty: string
  status: LessonStatus
  progress: number
  description: string
}

export const lessons: Lesson[] = [
  {
    sequence: 1,
    topic: '机器学习基础',
    difficulty: '入门',
    status: 'completed',
    progress: 100,
    description: '掌握机器学习的基本概念、监督学习与非监督学习的区别',
  },
  {
    sequence: 2,
    topic: '神经网络基础',
    difficulty: '初级',
    status: 'completed',
    progress: 100,
    description: '理解神经元、激活函数、前向传播与反向传播原理',
  },
  {
    sequence: 3,
    topic: '深度学习原理',
    difficulty: '中级',
    status: 'in_progress',
    progress: 65,
    description: '深入学习深度神经网络的训练技巧与优化方法',
  },
  {
    sequence: 4,
    topic: 'CNN架构',
    difficulty: '中级',
    status: 'pending',
    progress: 0,
    description: '掌握卷积神经网络的核心原理与图像处理应用',
  },
  {
    sequence: 5,
    topic: 'Transformer架构',
    difficulty: '高级',
    status: 'pending',
    progress: 0,
    description: '理解自注意力机制与Transformer的核心创新',
  },
  {
    sequence: 6,
    topic: '大模型微调技术',
    difficulty: '精通',
    status: 'pending',
    progress: 0,
    description: '学习LoRA、P-Tuning等高效微调方法',
  },
]

/** 下一节课 = 第一节未完成（进行中优先，其次待学习）；全部完成返回 null */
export function nextLesson(list: Lesson[] = lessons): Lesson | null {
  return (
    list.find((l) => l.status === 'in_progress') ??
    list.find((l) => l.status === 'pending') ??
    null
  )
}

/** 路径是否全部学完（用于闭环回流到复测） */
export function allLessonsDone(list: Lesson[] = lessons): boolean {
  return list.every((l) => l.status === 'completed')
}
