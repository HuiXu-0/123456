import { useEffect, useRef } from 'react'
import * as echarts from 'echarts'
import { useChartTokens, withAlpha } from '../../utils/chartTheme'
import './RadarChart.css'

interface RadarChartProps {
  data: {
    dimensions: string[]
    values: number[]
  }
  /** 对标线（按维度），如实时岗位要求；不传则用默认目标线 */
  target?: number[]
  targetName?: string
}

export default function RadarChart({ data, target, targetName }: RadarChartProps) {
  const chartRef = useRef<HTMLDivElement>(null)
  const chartInstance = useRef<echarts.ECharts | null>(null)
  const t = useChartTokens()

  useEffect(() => {
    if (!chartRef.current) return

    /* 数据未就绪守卫：echarts 雷达 indicator 为空（或 values 与维度数不一致）会在
       radarLayout 阶段抛 "reading 'push'" 崩溃，且因父级无 error boundary 会连带
       卸载整个页面 → 首屏白屏。首帧 overview 尚未加载（dimensions=[]）时跳过渲染，
       待数据到达再画（仍保留已有实例，避免闪烁）。 */
    const dims = data?.dimensions ?? []
    const vals = data?.values ?? []
    if (dims.length === 0 || vals.length !== dims.length) {
      chartInstance.current?.clear()
      return
    }

    if (!chartInstance.current) {
      chartInstance.current = echarts.init(chartRef.current)
    }

    const option = {
      radar: {
        center: ['50%', '42%'],
        radius: '65%',
        indicator: data.dimensions.map(name => ({
          name,
          max: 100
        })),
        shape: 'polygon',
        splitNumber: 5,
        axisName: {
          color: t.inkSoft,
          fontSize: 11,
          fontWeight: 500,
          overflow: 'truncate',
          width: 80
        },
        axisNameGap: 16,
        /* 半透明网格线（主题中性线色）*/
        splitLine: {
          lineStyle: {
            color: withAlpha(t.inkSoft, 0.22),
            width: 1
          }
        },
        splitArea: {
          show: true,
          areaStyle: {
            color: [
              withAlpha(t.surface, 0),
              withAlpha(t.surface2, 0.5),
              withAlpha(t.surface, 0),
              withAlpha(t.surface2, 0.32),
              withAlpha(t.surface, 0)
            ]
          }
        },
        axisLine: {
          lineStyle: {
            color: withAlpha(t.inkSoft, 0.15)
          }
        }
      },
      series: [
        {
          type: 'radar',
          data: [
            {
              value: data.values,
              name: '当前能力',
              symbol: 'circle',
              symbolSize: 7,
              /* 主色渐变线条（primary → primary-d）*/
              lineStyle: {
                color: new echarts.graphic.LinearGradient(0, 0, 1, 1, [
                  { offset: 0, color: t.primary },
                  { offset: 1, color: t.primaryD }
                ]),
                width: 2.5,
                shadowColor: withAlpha(t.primary, 0.5),
                shadowBlur: 12,
                shadowOffsetY: 0
              },
              /* 主色径向渐变填充 */
              areaStyle: {
                color: new echarts.graphic.RadialGradient(0.5, 0.5, 1, [
                  { offset: 0, color: withAlpha(t.primary, 0.32) },
                  { offset: 0.6, color: withAlpha(t.primary, 0.14) },
                  { offset: 1, color: withAlpha(t.primary, 0.02) }
                ])
              },
              itemStyle: {
                color: t.primary,
                borderColor: t.surface,
                borderWidth: 2,
                shadowColor: withAlpha(t.primary, 0.6),
                shadowBlur: 10
              }
            },
            {
              value: target ?? data.dimensions.map(() => 85),
              name: targetName ?? '目标能力',
              symbol: 'circle',
              symbolSize: 4,
              /* 目标线 = 强调色虚线 */
              lineStyle: {
                color: withAlpha(t.accent, 0.6),
                width: 1.5,
                type: 'dashed',
                shadowColor: withAlpha(t.accent, 0.3),
                shadowBlur: 6
              },
              areaStyle: {
                color: new echarts.graphic.RadialGradient(0.5, 0.5, 1, [
                  { offset: 0, color: withAlpha(t.accent, 0.12) },
                  { offset: 1, color: withAlpha(t.accent, 0.01) }
                ])
              },
              itemStyle: {
                color: withAlpha(t.accent, 0.7),
                borderColor: t.surface,
                borderWidth: 1,
                shadowColor: withAlpha(t.accent, 0.4),
                shadowBlur: 6
              }
            }
          ]
        }
      ],
      legend: {
        bottom: '0%',
        itemWidth: 12,
        itemHeight: 12,
        textStyle: {
          fontSize: 11,
          color: t.inkSoft
        }
      },
      grid: {
        bottom: 40
      },
      animation: true,
      animationDuration: 1200,
      animationEasing: 'cubicOut' as const
    }

    chartInstance.current.setOption(option as echarts.EChartsOption)

    const handleResize = () => {
      chartInstance.current?.resize()
    }

    window.addEventListener('resize', handleResize)

    return () => {
      window.removeEventListener('resize', handleResize)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, t, target, targetName])

  return (
    <div className="radar-chart" ref={chartRef} />
  )
}
