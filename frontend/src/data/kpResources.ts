/* 每知识点的 mock 学习内容（讲义三档 / 测验 / 思维导图 / 知识图解）。
 *
 * 背景：mock 模式（无后端联调）下学习资源页此前只有「神经网络基础(nn)」一套内容，
 * 故曾把 kpId 写死为 nn——导致从学习路径点任意知识点都落到神经网络。这里为其余知识点
 * 补齐对应内容，使「点不同知识点 → 看对应内容」在纯 mock 下也成立；nn 仍沿用
 * LearningResource.tsx 内既有的精修常量（作为兜底/默认）。
 *
 * 联调模式（USE_REAL_API=true）不使用本文件，内容仍由后端按 kpId 真实生成。
 */
import type { QuizQuestion } from '../components/QuizRenderer'
import type { LectureScene } from '../remotion/LectureVideo'
import { ksPointById } from './knowledgeSystem'

export interface KpResourceContent {
  /** 难度自适应三档讲义（入门/初级/高级），与难度切换 UI 对齐 */
  lectureByLevel: Record<'入门' | '初级' | '高级', string>
  /** 分阶测试题（mock 本地判分） */
  quiz: QuizQuestion[]
  /** 思维导图大纲（markdown 标题层级） */
  mindmap: string
  /** 知识图解（mermaid flowchart） */
  diagram: string
  /** 代码实操：浏览器内可跑的知识点 demo（本地 srcdoc 沙箱执行，见 CodeSandbox） */
  code: KpCodeDemo
  /** 讲解视频 mock 分镜（联调走 8.3 POST /resource/video 真实生成；nn 沿用 LectureVideo 内默认分镜） */
  video: KpVideoScript
}

/** 讲解视频 mock 分镜脚本：title=视频标题（知识点名），scenes 与接口 8.3 结构一致。 */
export interface KpVideoScript {
  title: string
  scenes: LectureScene[]
}

/** 代码实操 demo：hint 为顶部「怎么用」提示，js 为 index.js 内容（在本地 iframe srcdoc 内执行）。 */
export interface KpCodeDemo {
  hint: string
  js: string
}

/* ═══════════════════ 讲解视频 mock 分镜（5 核心点各 5 幕；nn 沿用 LectureVideo 默认分镜）═══════════════════
   结构对齐后端 8.3：每幕 title + points[3] + 一句旁白；首幕课程导入、末幕学习闭环，与 nn 脚本同体例。 */

const ML_VIDEO: KpVideoScript = {
  title: '机器学习基础',
  scenes: [
    {
      title: '课程导入 · 机器学习基础',
      points: ['从数据中自动找规律', '监督 · 无监督 · 强化', '由领域知识生成智能体定制'],
      narration: '欢迎学习机器学习基础。本视频由领域知识生成智能体为你定制。',
    },
    {
      title: '机器学习在做什么',
      points: ['传统编程：人写规则', '机器学习：从样本归纳规律', '用学到的规律预测新数据'],
      narration: '传统编程由人来写规则，机器学习则从大量样本中自动归纳规律，再用规律预测新数据。',
    },
    {
      title: '三大学习范式',
      points: ['监督学习：例子带标签', '无监督学习：自己找结构', '强化学习：试错换奖励'],
      narration: '监督学习从带标签的例子学映射，无监督学习自己发现结构，强化学习在试错中学习策略。',
    },
    {
      title: '训练与泛化',
      points: ['训练集上拟合规律', '测试集上检验泛化', '过拟合：记住了却不会用'],
      narration: '模型在训练集上学习，在测试集上检验泛化能力；只会背训练集叫过拟合。',
    },
    {
      title: '学习闭环',
      points: ['数据 → 模型 → 评估', '误差驱动持续改进', '完成测验巩固理解'],
      narration: '数据训练模型、评估暴露误差、误差驱动改进，构成机器学习的完整闭环。',
    },
  ],
}

const DL_VIDEO: KpVideoScript = {
  title: '深度学习原理',
  scenes: [
    {
      title: '课程导入 · 深度学习原理',
      points: ['多层网络为何更强', '反向传播与梯度下降', '由领域知识生成智能体定制'],
      narration: '欢迎学习深度学习原理。本视频由领域知识生成智能体为你定制。',
    },
    {
      title: '从浅层到深层',
      points: ['逐层提取更抽象特征', '低层边缘 → 高层语义', '端到端自动学特征'],
      narration: '深层网络逐层提取特征，低层看到边缘纹理，高层理解语义，无需人工设计特征。',
    },
    {
      title: '反向传播',
      points: ['损失函数衡量差距', '链式法则回传梯度', '逐层更新权重'],
      narration: '损失函数衡量预测与真实的差距，反向传播用链式法则把梯度逐层回传，指导权重更新。',
    },
    {
      title: '梯度下降与优化器',
      points: ['沿梯度反方向走一步', '学习率决定步长', 'SGD / Adam 等优化器'],
      narration: '梯度下降沿梯度反方向更新参数，学习率决定步长，Adam 等优化器让下降更稳更快。',
    },
    {
      title: '学习闭环',
      points: ['前向算出损失', '反向更新权重', '完成测验巩固理解'],
      narration: '前向传播算损失、反向传播更新权重，循环往复直到收敛，这就是深度学习的核心闭环。',
    },
  ],
}

const CNN_VIDEO: KpVideoScript = {
  title: 'CNN架构',
  scenes: [
    {
      title: '课程导入 · CNN架构',
      points: ['卷积核提取局部特征', '卷积 → 池化 → 全连接', '由领域知识生成智能体定制'],
      narration: '欢迎学习卷积神经网络架构。本视频由领域知识生成智能体为你定制。',
    },
    {
      title: '卷积层',
      points: ['卷积核滑窗做点积', '参数共享大幅省参数', '多个核提取多种特征'],
      narration: '卷积核在图像上滑窗做点积提取局部特征，参数共享让模型远小于全连接网络。',
    },
    {
      title: '池化与降采样',
      points: ['最大池化保留显著特征', '缩小尺寸、扩大感受野', '提升平移鲁棒性'],
      narration: '池化层缩小特征图尺寸并扩大感受野，最大池化保留最显著的响应，对平移更鲁棒。',
    },
    {
      title: '经典结构范式',
      points: ['卷积-池化交替堆叠', '末端全连接做分类', 'LeNet → AlexNet → ResNet'],
      narration: '经典 CNN 由卷积和池化交替堆叠、末端接全连接分类，从 LeNet 演进到 ResNet。',
    },
    {
      title: '学习闭环',
      points: ['局部感受野层层抽象', '特征图可视化验证理解', '完成测验巩固理解'],
      narration: '从局部感受野到层层抽象的特征图，理解卷积的工作方式后，完成测验巩固理解。',
    },
  ],
}

const TRANSFORMER_VIDEO: KpVideoScript = {
  title: 'Transformer架构',
  scenes: [
    {
      title: '课程导入 · Transformer架构',
      points: ['全靠注意力机制', '抛弃循环与卷积', '由领域知识生成智能体定制'],
      narration: '欢迎学习 Transformer 架构。本视频由领域知识生成智能体为你定制。',
    },
    {
      title: '自注意力机制',
      points: ['Query·Key 算相关性', 'softmax 归一为权重', '加权聚合 Value'],
      narration: '自注意力用查询与键的点积衡量相关性，softmax 归一化成权重，再加权聚合值向量。',
    },
    {
      title: '多头与位置编码',
      points: ['多头关注不同关系', '位置编码补充顺序信息', '残差连接与层归一化'],
      narration: '多头注意力从不同角度建模关系，位置编码补充顺序信息，残差与层归一化稳定训练。',
    },
    {
      title: '编码器与解码器',
      points: ['编码器理解输入序列', '解码器逐步生成输出', '大语言模型的基石'],
      narration: '编码器负责理解输入，解码器逐步生成输出，这一架构成为大语言模型的基石。',
    },
    {
      title: '学习闭环',
      points: ['注意力权重可解释', '并行计算训练高效', '完成测验巩固理解'],
      narration: '注意力权重可视化让模型可解释，并行计算带来高效训练，完成测验巩固理解。',
    },
  ],
}

const FINETUNE_VIDEO: KpVideoScript = {
  title: '大模型微调技术',
  scenes: [
    {
      title: '课程导入 · 大模型微调技术',
      points: ['预训练 + 微调范式', '冻结与解冻的权衡', '由领域知识生成智能体定制'],
      narration: '欢迎学习大模型微调技术。本视频由领域知识生成智能体为你定制。',
    },
    {
      title: '为什么要微调',
      points: ['通用能力来自预训练', '少量数据适配下游任务', '成本远低于从头训练'],
      narration: '预训练赋予模型通用能力，微调用少量任务数据完成适配，成本远低于从头训练。',
    },
    {
      title: '全量微调与冻结',
      points: ['全参数更新：效果好开销大', '冻结主干只训任务头', '折中：解冻顶部几层'],
      narration: '全量微调更新所有参数，开销最大；冻结主干只训练任务头最省；解冻顶部几层是折中。',
    },
    {
      title: '参数高效微调',
      points: ['LoRA：低秩增量矩阵', 'Adapter：插入小模块', '推理可合并零延迟'],
      narration: '参数高效微调只训练极小的增量参数，LoRA 用低秩矩阵，训练后可合并进原模型。',
    },
    {
      title: '学习闭环',
      points: ['按算力与数据选策略', '评估防灾难性遗忘', '完成测验巩固理解'],
      narration: '按算力和数据量选择微调策略，并评估防止灾难性遗忘，完成测验巩固理解。',
    },
  ],
}

/* ═══════════════════════ 代码实操 demo（6 核心点各一份，真实可跑）═══════════════════════
   约定：渲染到 #app；纯浏览器 JS 无依赖；不用模板字符串（避免与外层 TS 模板字面量转义纠缠）；
   页面底色/文字色由沙箱 srcdoc 按平台主题注入，demo 内只用局部装饰色。 */

const ML_CODE: KpCodeDemo = {
  hint: '改动 learningRate / epochs / data 数据点，看梯度下降拟合出的 w、b 与损失变化：',
  js: `// 线性回归：用梯度下降从数据里"学"出规律 y ≈ w·x + b
var data = [[1, 3.1], [2, 4.9], [3, 7.2], [4, 8.8], [5, 11.1]]; // [x, y] 样本
var learningRate = 0.02;  // 学习率
var epochs = 200;         // 训练轮数

var w = 0, b = 0;         // 从"什么都不会"开始
var trace = [];
for (var e = 1; e <= epochs; e++) {
  var gw = 0, gb = 0, loss = 0;
  for (var i = 0; i < data.length; i++) {
    var xi = data[i][0], yi = data[i][1];
    var err = (w * xi + b) - yi;          // 预测 − 真实
    gw += (2 * err * xi) / data.length;   // ∂loss/∂w
    gb += (2 * err) / data.length;        // ∂loss/∂b
    loss += (err * err) / data.length;    // 均方误差
  }
  w -= learningRate * gw;                 // 沿梯度反方向走一步
  b -= learningRate * gb;
  if (e === 1 || e % 50 === 0) {
    trace.push('第 ' + e + ' 轮  loss=' + loss.toFixed(3) + '  w=' + w.toFixed(2) + '  b=' + b.toFixed(2));
  }
}

var rows = data.map(function (d) {
  return '<tr><td>' + d[0] + '</td><td>' + d[1] + '</td><td>' + (w * d[0] + b).toFixed(2) + '</td></tr>';
}).join('');

document.getElementById('app').innerHTML =
  '<h3 style="margin:0 0 8px">📈 线性回归 · 梯度下降</h3>' +
  '<p>学到的规律：<b>y ≈ ' + w.toFixed(2) + ' · x + ' + b.toFixed(2) + '</b>（数据由 y≈2x+1 加噪生成）</p>' +
  '<pre style="opacity:.75;font-size:12px;line-height:1.7;margin:8px 0">' + trace.join('\\n') + '</pre>' +
  '<table style="border-collapse:collapse;font-size:13px">' +
  '<tr><th style="padding:2px 12px;text-align:left">x</th><th style="padding:2px 12px;text-align:left">真实 y</th><th style="padding:2px 12px;text-align:left">预测 ŷ</th></tr>' +
  rows.replace(/<td>/g, '<td style="padding:2px 12px;border-top:1px solid rgba(127,127,127,.35)">') +
  '</table>' +
  '<p style="opacity:.6;font-size:12px;margin-top:10px">✏️ 把 learningRate 改成 0.1 试试——损失还降得下去吗？</p>';
`,
}

const DL_CODE: KpCodeDemo = {
  hint: '改动两组学习率 lrSmall / lrLarge 或起点 wStart，对比梯度下降是收敛还是震荡：',
  js: `// 梯度下降怎么"走"：在损失函数 L(w) = (w − 3)² 上，从 w = −2 出发下山
function loss(w) { return (w - 3) * (w - 3); }
function grad(w) { return 2 * (w - 3); }   // dL/dw：反向传播算出的梯度

var wStart = -2;
var lrSmall = 0.1;    // 小学习率：稳步收敛
var lrLarge = 0.95;   // 大学习率：来回震荡
var steps = 8;

function descend(lr) {
  var w = wStart, path = [];
  for (var s = 0; s <= steps; s++) {
    path.push('step ' + s + '  w=' + w.toFixed(3) + '  loss=' + loss(w).toFixed(3));
    w = w - lr * grad(w);                  // 优化器的一步：w ← w − lr·∇L
  }
  return path;
}

function column(title, lr) {
  return '<div style="flex:1;min-width:200px">' +
    '<div style="font-weight:700;margin-bottom:4px">' + title + '（lr=' + lr + '）</div>' +
    '<pre style="font-size:12px;line-height:1.7;margin:0;opacity:.8">' + descend(lr).join('\\n') + '</pre>' +
  '</div>';
}

document.getElementById('app').innerHTML =
  '<h3 style="margin:0 0 8px">⛰️ 梯度下降与学习率（最低点在 w=3）</h3>' +
  '<div style="display:flex;gap:20px;flex-wrap:wrap">' +
  column('小步稳走', lrSmall) + column('大步震荡', lrLarge) +
  '</div>' +
  '<p style="opacity:.6;font-size:12px;margin-top:10px">✏️ 把 lrLarge 改成 1.05——loss 会发散（越走越高）；这就是训练"炸了"。</p>';
`,
}

const NN_CODE: KpCodeDemo = {
  hint: '改动 weights / bias / 激活函数，右侧实时看输出变化：',
  js: `// 单个神经元的前向传播：加权求和 → 加偏置 → ReLU 激活
function relu(v) { return Math.max(0, v); }

function neuron(inputs, weights, bias) {
  var z = bias;
  for (var i = 0; i < inputs.length; i++) z += inputs[i] * weights[i];
  return { z: z, out: relu(z) };
}

var inputs  = [0.5, 0.8, 0.2];   // 输入
var weights = [0.4, 0.7, 0.1];   // 权重
var bias    = 0.1;               // 偏置

var r = neuron(inputs, weights, bias);

// 渲染到页面，直观看到每一步
document.getElementById('app').innerHTML =
  '<h3 style="margin:0 0 8px">🧠 神经元前向传播</h3>' +
  '<div>输入 inputs = [' + inputs.join(', ') + ']</div>' +
  '<div>权重 weights = [' + weights.join(', ') + ']</div>' +
  '<div>偏置 bias = ' + bias + '</div>' +
  '<div style="margin-top:6px">加权求和 z = ' + r.z.toFixed(2) + '</div>' +
  '<div style="margin-top:8px;font-weight:700;color:#4f8b6f">输出 = ReLU(z) = ' + r.out.toFixed(2) + '</div>' +
  '<p style="opacity:.6;font-size:12px;margin-top:12px">✏️ 试着改改 weights / bias，或把 ReLU 换成 v => 1/(1+Math.exp(-v))（Sigmoid）。</p>';
`,
}

const CNN_CODE: KpCodeDemo = {
  hint: '改动 3×3 kernel 的数值（当前为竖直边缘检测 Sobel 核），看特征图如何响应：',
  js: `// 卷积：3×3 卷积核在 6×6 图像上滑窗做点积，提取"竖直边缘"特征图
var image = [            // 左半暗(0)右半亮(9)，中间有一条竖直边缘
  [0, 0, 0, 9, 9, 9],
  [0, 0, 0, 9, 9, 9],
  [0, 0, 0, 9, 9, 9],
  [0, 0, 0, 9, 9, 9],
  [0, 0, 0, 9, 9, 9],
  [0, 0, 0, 9, 9, 9],
];
var kernel = [           // Sobel 竖直边缘检测核
  [1, 0, -1],
  [2, 0, -2],
  [1, 0, -1],
];

function convolve(img, k) {         // 滑窗：每个位置 = 3×3 邻域与核的点积
  var out = [];
  for (var y = 0; y + 3 <= img.length; y++) {
    var row = [];
    for (var x = 0; x + 3 <= img[0].length; x++) {
      var sum = 0;
      for (var i = 0; i < 3; i++) for (var j = 0; j < 3; j++) sum += img[y + i][x + j] * k[i][j];
      row.push(sum);
    }
    out.push(row);
  }
  return out;
}

var featureMap = convolve(image, kernel);

function grid(m, maxAbs, title) {   // 数值 → 灰度格子（越亮响应越强）
  var html = '<div style="display:inline-block;margin:0 18px 10px 0;vertical-align:top">' +
    '<div style="font-size:12px;opacity:.7;margin-bottom:4px">' + title + '</div>';
  for (var y = 0; y < m.length; y++) {
    html += '<div style="line-height:0">';
    for (var x = 0; x < m[y].length; x++) {
      var g = Math.round(255 * Math.min(1, Math.abs(m[y][x]) / maxAbs));
      html += '<span style="display:inline-block;width:26px;height:26px;background:rgb(' + g + ',' + g + ',' + g + ');' +
        'color:' + (g > 140 ? '#222' : '#ddd') + ';font-size:10px;line-height:26px;text-align:center">' + m[y][x] + '</span>';
    }
    html += '</div>';
  }
  return html + '</div>';
}

document.getElementById('app').innerHTML =
  '<h3 style="margin:0 0 10px">🔍 卷积核滑窗 · 边缘检测</h3>' +
  grid(image, 9, '输入图像 6×6') + grid(kernel, 2, '卷积核 3×3') + grid(featureMap, 36, '特征图 4×4') +
  '<p style="opacity:.6;font-size:12px;margin-top:6px">✏️ 把 kernel 转置成横向核 [[1,2,1],[0,0,0],[-1,-2,-1]]——竖直边缘就检不出来了（特征图全 0）。</p>';
`,
}

const TRANSFORMER_CODE: KpCodeDemo = {
  hint: '改动各词的 key 向量、query 或 temperature，看注意力权重（softmax）如何重新分配：',
  js: `// 自注意力：query 与每个词的 key 算相似度（点积）→ softmax → 得到注意力权重
var tokens = [
  { word: '小猫', key: [0.9, 0.1, 0.0] },
  { word: '吃',   key: [0.1, 0.8, 0.1] },
  { word: '鱼',   key: [0.7, 0.3, 0.1] },
  { word: '了',   key: [0.0, 0.1, 0.9] },
];
var query = [0.8, 0.2, 0.1];   // 当前词想"找什么"（试试改成 [0,0.9,0.2] 去关注动词）
var temperature = 0.3;         // 越小分布越尖锐（对应 1/√d 缩放的作用）

function dot(a, b) { var s = 0; for (var i = 0; i < a.length; i++) s += a[i] * b[i]; return s; }

var scores = tokens.map(function (t) { return dot(query, t.key) / temperature; });
var maxS = Math.max.apply(null, scores);
var exps = scores.map(function (s) { return Math.exp(s - maxS); });
var sumE = exps.reduce(function (a, b) { return a + b; }, 0);
var weights = exps.map(function (e) { return e / sumE; });   // softmax：总和恰为 1

var rows = tokens.map(function (t, i) {
  var pct = (weights[i] * 100).toFixed(1);
  return '<div style="display:flex;align-items:center;gap:8px;margin:4px 0">' +
    '<span style="width:44px">' + t.word + '</span>' +
    '<span style="width:110px;font-size:12px;opacity:.7">score ' + scores[i].toFixed(2) + '</span>' +
    '<span style="flex:1;background:rgba(127,127,127,.18);border-radius:4px;overflow:hidden">' +
      '<span style="display:block;height:14px;width:' + pct + '%;background:#4f8b6f"></span></span>' +
    '<b style="width:56px;text-align:right">' + pct + '%</b>' +
  '</div>';
}).join('');

document.getElementById('app').innerHTML =
  '<h3 style="margin:0 0 8px">🎯 自注意力权重分配</h3>' +
  '<p style="font-size:13px;opacity:.8">query 在整句「' + tokens.map(function (t) { return t.word; }).join('') + '」上的注意力：</p>' +
  rows +
  '<p style="opacity:.6;font-size:12px;margin-top:10px">✏️ 权重和恒为 1（softmax）。把 temperature 调大到 2——注意力会被"摊平"。</p>';
`,
}

const FINETUNE_CODE: KpCodeDemo = {
  hint: '切换各层 frozen（❄冻结/🔥参与训练）标记，对比不同微调策略的可训练参数量：',
  js: `// 微调策略：冻结哪些层，决定要训练/存储多少参数（单位：百万 M）
var layers = [
  { name: 'Embedding 词嵌入',        params: 38.0, frozen: true  },
  { name: 'Transformer 底部 10 层',  params: 85.0, frozen: true  },
  { name: 'Transformer 顶部 2 层',   params: 17.0, frozen: false },
  { name: '任务分类头 Head',         params: 0.6,  frozen: false },
];

var total = 0, trainable = 0;
layers.forEach(function (l) { total += l.params; if (!l.frozen) trainable += l.params; });
var pct = ((trainable / total) * 100).toFixed(1);

var rows = layers.map(function (l) {
  return '<tr>' +
    '<td style="padding:4px 12px;border-top:1px solid rgba(127,127,127,.35)">' + l.name + '</td>' +
    '<td style="padding:4px 12px;border-top:1px solid rgba(127,127,127,.35);text-align:right">' + l.params.toFixed(1) + ' M</td>' +
    '<td style="padding:4px 12px;border-top:1px solid rgba(127,127,127,.35)">' + (l.frozen ? '❄ 冻结' : '🔥 训练') + '</td>' +
  '</tr>';
}).join('');

document.getElementById('app').innerHTML =
  '<h3 style="margin:0 0 8px">🧊 微调 · 冻结与解冻</h3>' +
  '<table style="border-collapse:collapse;font-size:13px">' +
  '<tr><th style="padding:4px 12px;text-align:left">层</th><th style="padding:4px 12px;text-align:right">参数量</th><th style="padding:4px 12px;text-align:left">状态</th></tr>' +
  rows + '</table>' +
  '<p style="margin-top:10px">可训练参数：<b>' + trainable.toFixed(1) + ' M / ' + total.toFixed(1) + ' M（' + pct + '%）</b>' +
  '——梯度与优化器状态只为 🔥 层分配，显存/算力开销随之线性下降。</p>' +
  '<p style="opacity:.6;font-size:12px">✏️ 全部设为 frozen:false 就是"全量微调"；LoRA 则相当于再把 🔥 层换成低秩增量（参数量再降一个数量级）。</p>';
`,
}

/* ─────────────────────────── 机器学习基础 (ml) ─────────────────────────── */
const ml: KpResourceContent = {
  code: ML_CODE,
  video: ML_VIDEO,
  lectureByLevel: {
    入门: `# 机器学习基础（入门版）

> 本讲义由**领域知识生成 Agent**按「入门」难度生成——用最直白的话讲清「机器是怎么学的」。

## 一、机器学习在做什么

传统编程是人写规则；机器学习是**给机器大量例子，让它自己总结规律**。
比如给它很多「邮件 + 是否垃圾邮件」的例子，它就能学会判断新邮件。

## 二、三种常见类型

- **监督学习**：例子带答案（标签），学「输入→输出」的映射，如分类、回归。
- **无监督学习**：例子没答案，自己找结构，如聚类。
- **强化学习**：在环境中试错，靠奖励学策略。

> **一句话**：机器学习 = 从数据里自动找规律，再用规律预测新数据。`,

    初级: `# 机器学习基础（初级版）

> 本讲义由**领域知识生成 Agent**生成，难度已适配为「初级」，并经**内容审核 Agent** RAG 交叉校验。

## 一、什么是机器学习

机器学习（Machine Learning）让计算机从数据中**自动学习规律**，而非依赖人工编写的固定规则。其核心是用一个带参数的**模型**去拟合数据，再用它对新样本做预测。

## 二、监督学习的基本流程

1. 准备带标签的**训练集** \`(x, y)\`
2. 选择模型与**损失函数**（衡量预测与真值的差距）
3. 通过优化算法**最小化损失**，得到最优参数
4. 在**测试集**上评估泛化能力

\`\`\`python
from sklearn.linear_model import LogisticRegression
clf = LogisticRegression()
clf.fit(X_train, y_train)        # 训练：从数据学参数
acc = clf.score(X_test, y_test)  # 评估泛化
\`\`\`

## 三、过拟合与欠拟合

- **欠拟合**：模型太简单，训练/测试都差 → 提升模型复杂度。
- **过拟合**：训练好、测试差，把噪声也背了下来 → 正则化、增样本、早停。

> **小结**：数据 → 模型 → 损失 → 优化 → 评估，是一切监督学习任务的统一闭环。下一步建议学习「神经网络基础」。`,

    高级: `# 机器学习基础（高级版）

> 本讲义由**领域知识生成 Agent**按「高级」难度生成——侧重形式化与偏差/方差权衡。

## 一、学习问题的形式化

给定分布 \`D\` 上的样本 \`(x, y)\`，目标是找到假设 \`h\` 最小化期望风险：

\`\`\`
R(h) = E_{(x,y)~D}[ L(h(x), y) ]
\`\`\`

实践中只能最小化经验风险 \`R_emp(h) = (1/n) Σ L(h(x_i), y_i)\`。

## 二、偏差—方差分解

泛化误差 ≈ **偏差² + 方差 + 噪声**：

- 高偏差 → 欠拟合（模型容量不足）
- 高方差 → 过拟合（对训练集波动敏感）

正则化（L1/L2）、交叉验证、集成都是在这条权衡线上做取舍。

## 三、工程要点

- **特征工程 / 标准化**对线性模型影响显著
- **交叉验证**做无偏的超参选择
- **数据泄漏**是最常见、最隐蔽的坑

> **小结**：理解经验风险最小化与偏差/方差权衡，是调好任何模型的底层心法。下一步建议「神经网络基础 / 深度学习原理」。`,
  },
  quiz: [
    {
      question_id: 'ml_q1', question_type: 'single',
      question_text: '下列哪种任务属于监督学习？',
      options: [
        { option_id: 'a', option_text: '把无标签用户分成若干群体' },
        { option_id: 'b', option_text: '根据带标签的房屋数据预测房价' },
        { option_id: 'c', option_text: '让智能体在游戏中试错获取奖励' },
      ],
      correct_answer: 'b',
      explanation: '监督学习使用带标签数据学习输入到输出的映射；预测房价是典型的回归（监督学习）。',
    },
    {
      question_id: 'ml_q2', question_type: 'boolean',
      question_text: '过拟合的典型表现是训练集表现很好但测试集表现明显变差。',
      options: [{ option_id: 'true', option_text: '正确' }, { option_id: 'false', option_text: '错误' }],
      correct_answer: 'true',
      explanation: '过拟合即模型把训练集的噪声也学了进去，泛化能力下降，表现为训练好、测试差。',
    },
    {
      question_id: 'ml_q3', question_type: 'multiple',
      question_text: '以下哪些手段有助于缓解过拟合？（多选）',
      options: [
        { option_id: 'a', option_text: '增加正则化项' },
        { option_id: 'b', option_text: '增加训练数据' },
        { option_id: 'c', option_text: '提前停止训练（早停）' },
        { option_id: 'd', option_text: '一味增大模型复杂度' },
      ],
      correct_answer: ['a', 'b', 'c'],
      explanation: '正则化、增样本、早停都能抑制过拟合；盲目增大模型复杂度反而加剧过拟合。',
    },
    {
      question_id: 'ml_q4', question_type: 'single',
      question_text: '损失函数（loss）在训练中的作用是？',
      options: [
        { option_id: 'a', option_text: '衡量预测与真实值的差距，作为优化目标' },
        { option_id: 'b', option_text: '决定数据的存储格式' },
        { option_id: 'c', option_text: '随机打乱样本顺序' },
      ],
      correct_answer: 'a',
      explanation: '损失函数量化预测与真值的差距，优化算法通过最小化损失来更新模型参数。',
    },
  ],
  mindmap: `# 机器学习基础
## 学习范式
### 监督学习
### 无监督学习
### 强化学习
## 监督学习流程
### 训练集 (x,y)
### 损失函数
### 优化求参
### 测试集评估
## 模型困境
### 欠拟合
### 过拟合
#### 正则化
#### 早停
`,
  diagram: `flowchart LR
  D["训练数据 (x,y)"] --> M["模型 h(x;θ)"]
  M --> P["预测 ŷ"]
  P --> L{{"损失 L(ŷ,y)"}}
  L -. 反向优化更新 θ .-> M
  M --> E["测试集评估泛化"]
`,
}

/* ─────────────────────────── 深度学习原理 (dl) ─────────────────────────── */
const dl: KpResourceContent = {
  code: DL_CODE,
  video: DL_VIDEO,
  lectureByLevel: {
    入门: `# 深度学习原理（入门版）

> 本讲义由**领域知识生成 Agent**按「入门」难度生成——把「深」讲成「多搭几层」。

## 一、深度，深在哪

把很多层神经网络**叠起来**，就是深度学习。层数越多，能表达的规律越复杂——
浅层认边角，深层认整体。

## 二、为什么以前不流行

层一多就难训练：梯度会「消失」。后来有了 **ReLU 激活、更好的初始化、批归一化、
更大的数据和算力**，深层网络才真正跑起来。

> **一句话**：深度学习 = 多层网络 + 让它训得动的那些技巧。`,

    初级: `# 深度学习原理（初级版）

> 本讲义由**领域知识生成 Agent**生成，难度已适配为「初级」，并经**内容审核 Agent** RAG 交叉校验。

## 一、从浅层到深层

深度学习用**多层非线性变换**逐层提取特征：低层学边缘/纹理，高层学语义/概念。
层数带来的表达力，是它在图像、语音、文本上超越传统方法的关键。

## 二、训练深层网络的三大技巧

1. **激活函数 ReLU**：正区间梯度恒为 1，缓解梯度消失。
2. **批归一化（BatchNorm）**：稳定每层输入分布，加速收敛。
3. **残差连接（Residual）**：让梯度有「高速公路」直达浅层，可训练上百层。

\`\`\`python
import torch.nn as nn
block = nn.Sequential(
    nn.Linear(256, 256), nn.BatchNorm1d(256), nn.ReLU()
)
\`\`\`

## 三、正则化与优化

- **Dropout**：训练时随机丢弃神经元，抑制过拟合。
- **Adam**：自适应学习率，深层网络常用优化器。

> **小结**：深度 = 表达力，但要靠 ReLU / BN / 残差 / Dropout 才训得稳。下一步建议学习「CNN架构」。`,

    高级: `# 深度学习原理（高级版）

> 本讲义由**领域知识生成 Agent**按「高级」难度生成——侧重梯度流与归一化的数学动机。

## 一、梯度消失/爆炸

深层链式求导是连乘：\`∂L/∂a^(1) = Π_l W^(l)ᵀ diag(σ')\`。
若谱半径持续 <1 → 梯度消失；>1 → 爆炸。ReLU + He 初始化让方差逐层保持稳定。

## 二、残差网络的本质

\`y = x + F(x)\` 使雅可比含恒等项 \`I + ∂F/∂x\`，梯度可绕过非线性直接回传，
从而支持极深网络的优化。

## 三、归一化家族

- **BatchNorm**：按 batch 维归一，依赖较大 batch。
- **LayerNorm**：按特征维归一，序列模型/Transformer 首选。

> **小结**：深层可训练性 = 归一化稳分布 + 残差通梯度 + 自适应优化器。下一步建议「CNN / Transformer」。`,
  },
  quiz: [
    {
      question_id: 'dl_q1', question_type: 'single',
      question_text: '残差连接（y = x + F(x)）最主要解决的问题是？',
      options: [
        { option_id: 'a', option_text: '减少模型参数量' },
        { option_id: 'b', option_text: '缓解深层网络的梯度消失、便于优化' },
        { option_id: 'c', option_text: '对输入图像做数据增强' },
      ],
      correct_answer: 'b',
      explanation: '残差连接让梯度可经恒等路径直达浅层，缓解梯度消失，使极深网络可训练。',
    },
    {
      question_id: 'dl_q2', question_type: 'boolean',
      question_text: 'Dropout 通常在训练阶段随机丢弃部分神经元以抑制过拟合。',
      options: [{ option_id: 'true', option_text: '正确' }, { option_id: 'false', option_text: '错误' }],
      correct_answer: 'true',
      explanation: 'Dropout 在训练时随机置零部分神经元，减少协同适应，从而抑制过拟合；推理时关闭。',
    },
    {
      question_id: 'dl_q3', question_type: 'multiple',
      question_text: '以下哪些有助于训练更深的网络？（多选）',
      options: [
        { option_id: 'a', option_text: 'ReLU 激活函数' },
        { option_id: 'b', option_text: '批归一化 BatchNorm' },
        { option_id: 'c', option_text: '残差连接' },
        { option_id: 'd', option_text: '把所有权重初始化为 0' },
      ],
      correct_answer: ['a', 'b', 'c'],
      explanation: 'ReLU、BatchNorm、残差连接都利于深层训练；权重全 0 会破坏对称性、无法学习。',
    },
    {
      question_id: 'dl_q4', question_type: 'single',
      question_text: '深层网络中，高层特征相比低层特征通常更偏向于？',
      options: [
        { option_id: 'a', option_text: '边缘、纹理等底层细节' },
        { option_id: 'b', option_text: '语义、概念等高层抽象' },
        { option_id: 'c', option_text: '原始像素值' },
      ],
      correct_answer: 'b',
      explanation: '深层网络逐层抽象：低层捕捉边缘纹理，高层组合出语义/概念级特征。',
    },
  ],
  mindmap: `# 深度学习原理
## 为什么要深
### 逐层特征抽象
### 低层边缘→高层语义
## 可训练性技巧
### ReLU 激活
### 批归一化
### 残差连接
### Dropout
## 优化
### Adam 自适应
### 梯度消失/爆炸
`,
  diagram: `flowchart LR
  X["输入"] --> L1["层1: 边缘/纹理"]
  L1 --> L2["层2: 部件"]
  L2 --> L3["层3: 语义概念"]
  L3 --> Y["输出"]
  X -. 残差连接 .-> L2
  L1 -. 残差连接 .-> L3
`,
}

/* ─────────────────────────── CNN架构 (cnn) ─────────────────────────── */
const cnn: KpResourceContent = {
  code: CNN_CODE,
  video: CNN_VIDEO,
  lectureByLevel: {
    入门: `# CNN架构（入门版）

> 本讲义由**领域知识生成 Agent**按「入门」难度生成——用「滑动小窗口看图」来理解卷积。

## 一、CNN 在看什么

处理图像时，不必把每个像素都全连接。CNN 用一个**小窗口（卷积核）在图上滑动**，
每次只看一小块，找局部特征（边、角、纹理），再层层组合成完整物体。

## 二、三个关键词

- **卷积**：小窗口滑动提取局部特征。
- **池化**：缩小尺寸、保留主要信息，更抗位移。
- **权重共享**：同一个窗口走遍全图，参数少、效率高。

> **一句话**：CNN = 用滑动窗口高效地从图像里抽特征。`,

    初级: `# CNN架构（初级版）

> 本讲义由**领域知识生成 Agent**生成，难度已适配为「初级」，并经**内容审核 Agent** RAG 交叉校验。

## 一、卷积层

卷积核（如 3×3）在输入特征图上滑动，做**局部加权求和**得到输出特征图。
关键特性：**局部连接**与**权重共享**，大幅减少参数并保留空间结构。

\`\`\`python
import torch.nn as nn
conv = nn.Conv2d(in_channels=3, out_channels=16, kernel_size=3, padding=1)
# 输入 (N,3,H,W) → 输出 (N,16,H,W)
\`\`\`

## 二、池化层

**最大池化**取窗口内最大值，降采样、减少计算并增强平移不变性。

## 三、典型结构

\`[卷积 → ReLU → 池化] × N → 展平 → 全连接 → 分类\`。
经典网络 LeNet / AlexNet / VGG / ResNet 都遵循这一骨架并不断加深。

> **小结**：卷积抽局部特征、池化降维、全连接做决策——这就是 CNN 的图像处理范式。下一步建议学习「Transformer架构」。`,

    高级: `# CNN架构（高级版）

> 本讲义由**领域知识生成 Agent**按「高级」难度生成——侧重感受野与参数效率。

## 一、卷积的数学形式

输出特征图：\`Y[i,j] = Σ_{m,n} X[i+m, j+n] · K[m,n] + b\`。
输出尺寸：\`O = ⌊(W - F + 2P)/S⌋ + 1\`（W 输入、F 核、P 填充、S 步长）。

## 二、感受野

层层堆叠使深层每个神经元的**感受野**指数增大，从而「看到」更大范围的上下文。
空洞卷积（dilation）可在不增参数的情况下扩大感受野。

## 三、参数效率

权重共享 + 局部连接使卷积层参数量与图像尺寸**解耦**——这是 CNN 相对全连接网络
在视觉任务上高效的根本原因。1×1 卷积则用于跨通道信息融合与降维。

> **小结**：感受野决定「看多大」，权重共享决定「多省参数」。下一步建议「Transformer 架构」对比注意力机制。`,
  },
  quiz: [
    {
      question_id: 'cnn_q1', question_type: 'single',
      question_text: 'CNN 中「权重共享」带来的主要好处是？',
      options: [
        { option_id: 'a', option_text: '显著减少参数量并保留平移不变性' },
        { option_id: 'b', option_text: '让网络无法处理图像' },
        { option_id: 'c', option_text: '强制每个像素独立全连接' },
      ],
      correct_answer: 'a',
      explanation: '同一卷积核滑过全图（权重共享），参数量与图像大小解耦，并带来平移不变性。',
    },
    {
      question_id: 'cnn_q2', question_type: 'boolean',
      question_text: '最大池化（Max Pooling）能在降采样的同时增强一定的平移不变性。',
      options: [{ option_id: 'true', option_text: '正确' }, { option_id: 'false', option_text: '错误' }],
      correct_answer: 'true',
      explanation: '最大池化取局部窗口最大值，降低分辨率的同时使特征对小幅平移更鲁棒。',
    },
    {
      question_id: 'cnn_q3', question_type: 'single',
      question_text: '输入 32×32、卷积核 3×3、padding=1、stride=1，输出空间尺寸为？',
      options: [
        { option_id: 'a', option_text: '30×30' },
        { option_id: 'b', option_text: '32×32' },
        { option_id: 'c', option_text: '34×34' },
      ],
      correct_answer: 'b',
      explanation: 'O=(32-3+2×1)/1+1=32，padding=1 的 3×3 卷积保持空间尺寸不变。',
    },
    {
      question_id: 'cnn_q4', question_type: 'multiple',
      question_text: '关于卷积神经网络，下列说法正确的是？（多选）',
      options: [
        { option_id: 'a', option_text: '卷积层提取局部特征' },
        { option_id: 'b', option_text: '堆叠卷积层会增大感受野' },
        { option_id: 'c', option_text: '1×1 卷积可用于跨通道融合与降维' },
        { option_id: 'd', option_text: 'CNN 只能用于文本任务' },
      ],
      correct_answer: ['a', 'b', 'c'],
      explanation: '卷积抽局部特征、堆叠增大感受野、1×1 卷积做通道融合；CNN 主要用于视觉，也可用于其他网格数据，但「只能用于文本」错误。',
    },
  ],
  mindmap: `# CNN架构
## 卷积层
### 局部连接
### 权重共享
### 感受野
## 池化层
### 最大池化
### 降采样/平移不变
## 典型骨架
### 卷积→ReLU→池化
### 全连接分类
### LeNet/VGG/ResNet
`,
  diagram: `flowchart LR
  IMG["输入图像"] --> C1["卷积 + ReLU"]
  C1 --> P1["池化"]
  P1 --> C2["卷积 + ReLU"]
  C2 --> P2["池化"]
  P2 --> F["展平"]
  F --> FC["全连接"]
  FC --> O["分类输出"]
`,
}

/* ─────────────────────────── Transformer架构 (transformer) ─────────────────────────── */
const transformer: KpResourceContent = {
  code: TRANSFORMER_CODE,
  video: TRANSFORMER_VIDEO,
  lectureByLevel: {
    入门: `# Transformer架构（入门版）

> 本讲义由**领域知识生成 Agent**按「入门」难度生成——把「注意力」讲成「划重点」。

## 一、注意力是什么

读一句话时，理解某个词要参考句中**相关的其他词**。Transformer 让每个词都能
**主动去看**整句话里和自己最相关的部分，给它们更高权重——这就是「注意力」。

## 二、它强在哪

- 不像 RNN 逐词处理，Transformer **一次看全句**，可并行、训练快。
- 能捕捉**长距离**依赖（句首和句尾的关系也抓得住）。

> **一句话**：Transformer = 让每个词自动给全句「划重点」，再据此理解。`,

    初级: `# Transformer架构（初级版）

> 本讲义由**领域知识生成 Agent**生成，难度已适配为「初级」，并经**内容审核 Agent** RAG 交叉校验。

## 一、自注意力机制

每个 token 生成 **Query / Key / Value** 三个向量。用 Q 与所有 K 算相似度得到权重，
对 V 加权求和，得到融合了上下文的新表示：

\`\`\`
Attention(Q,K,V) = softmax(QKᵀ / √d_k) · V
\`\`\`

## 二、多头注意力

并行多组注意力（多个「头」），从不同子空间关注不同关系，再拼接——表达更丰富。

## 三、整体结构

\`输入嵌入 + 位置编码 → [多头注意力 + 前馈网络 + 残差&LayerNorm] × N\`。
因为没有循环结构，需用**位置编码**注入词序信息。

> **小结**：自注意力让全句信息一步交互，多头 + 位置编码 + 残差堆叠构成 Transformer。下一步建议学习「大模型微调技术」。`,

    高级: `# Transformer架构（高级版）

> 本讲义由**领域知识生成 Agent**按「高级」难度生成——侧重缩放点积与复杂度。

## 一、缩放点积注意力

\`softmax(QKᵀ/√d_k)V\` 中除以 \`√d_k\` 防止点积过大导致 softmax 饱和、梯度消失。
注意力矩阵 \`QKᵀ\` 大小为 \`n×n\`，复杂度 **O(n²·d)**，是长序列的主要瓶颈。

## 二、多头与子空间

\`head_i = Attention(QW_i^Q, KW_i^K, VW_i^V)\`，拼接后经 \`W^O\` 投影。
不同头可分别建模语法、指代、远距依赖等关系。

## 三、位置编码与变体

正弦位置编码 / 可学习位置 / 旋转位置编码（RoPE）。高效注意力（稀疏、线性、FlashAttention）
针对 O(n²) 瓶颈优化。

> **小结**：缩放点积稳梯度、多头扩表达、位置编码补序信息——这是大模型的统一底座。下一步建议「大模型微调技术」。`,
  },
  quiz: [
    {
      question_id: 'tr_q1', question_type: 'single',
      question_text: '缩放点积注意力中除以 √d_k 的主要作用是？',
      options: [
        { option_id: 'a', option_text: '防止点积过大使 softmax 饱和、梯度消失' },
        { option_id: 'b', option_text: '减少注意力头的数量' },
        { option_id: 'c', option_text: '把序列长度变为固定值' },
      ],
      correct_answer: 'a',
      explanation: '点积随维度增大而增大，除以 √d_k 做缩放可避免 softmax 进入饱和区、稳定梯度。',
    },
    {
      question_id: 'tr_q2', question_type: 'boolean',
      question_text: 'Transformer 没有循环结构，需要位置编码来注入词序信息。',
      options: [{ option_id: 'true', option_text: '正确' }, { option_id: 'false', option_text: '错误' }],
      correct_answer: 'true',
      explanation: '自注意力本身对顺序不敏感，必须通过位置编码引入 token 的次序信息。',
    },
    {
      question_id: 'tr_q3', question_type: 'multiple',
      question_text: '关于自注意力机制，下列正确的是？（多选）',
      options: [
        { option_id: 'a', option_text: '每个 token 生成 Q、K、V 三类向量' },
        { option_id: 'b', option_text: '可并行处理整个序列' },
        { option_id: 'c', option_text: '能捕捉长距离依赖' },
        { option_id: 'd', option_text: '其复杂度与序列长度无关' },
      ],
      correct_answer: ['a', 'b', 'c'],
      explanation: '自注意力用 Q/K/V、可并行、擅长长距离依赖；但注意力矩阵为 n×n，复杂度 O(n²) 与序列长度强相关。',
    },
    {
      question_id: 'tr_q4', question_type: 'single',
      question_text: '多头注意力相比单头的主要优势是？',
      options: [
        { option_id: 'a', option_text: '从多个子空间并行关注不同类型的关系' },
        { option_id: 'b', option_text: '彻底去掉前馈网络' },
        { option_id: 'c', option_text: '使模型无法并行' },
      ],
      correct_answer: 'a',
      explanation: '多头让模型在不同表示子空间并行学习不同关系（语法、指代、远距依赖等），表达更丰富。',
    },
  ],
  mindmap: `# Transformer架构
## 自注意力
### Query/Key/Value
### softmax(QKᵀ/√d_k)V
## 多头注意力
### 多子空间并行
### 拼接投影
## 整体结构
### 位置编码
### 前馈网络
### 残差 + LayerNorm
`,
  diagram: `flowchart LR
  E["输入嵌入 + 位置编码"] --> Q["Query"]
  E --> K["Key"]
  E --> V["Value"]
  Q --> A{{"softmax(QKᵀ/√d_k)"}}
  K --> A
  A --> Z["加权求和 · V"]
  V --> Z
  Z --> FFN["前馈网络 + 残差&LN"]
  FFN --> O["上下文表示"]
`,
}

/* ─────────────────────────── 大模型微调技术 (finetune) ─────────────────────────── */
const finetune: KpResourceContent = {
  code: FINETUNE_CODE,
  video: FINETUNE_VIDEO,
  lectureByLevel: {
    入门: `# 大模型微调技术（入门版）

> 本讲义由**领域知识生成 Agent**按「入门」难度生成——把微调讲成「在通才上补专业课」。

## 一、为什么要微调

大模型已经在海量数据上「读了万卷书」（预训练），但对你的**具体任务/领域**还不够专。
微调就是**用少量你的数据再训一训**，让它在你的场景上更好用。

## 二、省钱省力的做法

全部参数都重训太贵。**LoRA** 只训练很小一部分新增参数，效果接近、成本骤降，
是目前最常用的高效微调方法。

> **一句话**：微调 = 在预训练通才上，用少量数据补一门「专业课」。`,

    初级: `# 大模型微调技术（初级版）

> 本讲义由**领域知识生成 Agent**生成，难度已适配为「初级」，并经**内容审核 Agent** RAG 交叉校验。

## 一、为什么需要高效微调（PEFT）

大模型动辄数十亿参数，全量微调显存与算力开销巨大。**参数高效微调（PEFT）**只更新
极少量参数，即可让模型适配下游任务。

## 二、LoRA 原理

冻结原权重 \`W\`，在旁路注入低秩矩阵：\`W' = W + BA\`（\`B∈ℝ^{d×r}, A∈ℝ^{r×k}, r≪d\`）。
只训练 \`A、B\`，参数量从 \`d×k\` 降到 \`r×(d+k)\`。

\`\`\`python
from peft import LoraConfig, get_peft_model
cfg = LoraConfig(r=8, lora_alpha=16, target_modules=["q_proj", "v_proj"])
model = get_peft_model(base_model, cfg)  # 仅训练 LoRA 旁路
\`\`\`

## 三、其他方法

- **P-Tuning / Prompt-Tuning**：只训练少量「软提示」向量。
- **QLoRA**：4-bit 量化 + LoRA，单卡也能微调大模型。

> **小结**：冻结主干、只训小旁路，是大模型低成本落地的关键。这是路径的最后一步——恭喜你即将完成全程！`,

    高级: `# 大模型微调技术（高级版）

> 本讲义由**领域知识生成 Agent**按「高级」难度生成——侧重低秩假设与显存权衡。

## 一、低秩适配的假设

微调引起的权重更新 \`ΔW\` 往往是**低秩**的——LoRA 即用 \`ΔW = BA\`（秩 r）近似它。
推理时可将 \`BA\` 合并回 \`W\`，**不增加推理延迟**。

## 二、QLoRA 的关键

- **4-bit NF4 量化**冻结主干，显存大幅下降；
- 反传时**反量化**计算梯度，仅更新 LoRA 参数；
- **分页优化器**应对显存峰值。

## 三、选型权衡

\`r\` 越大表达力越强但参数/显存增加；\`lora_alpha\` 控制缩放。
全量微调效果上限更高，但 PEFT 在**成本/效果/可维护性**上往往更优。

> **小结**：低秩 + 量化把「微调大模型」从集群级拉到单卡级。至此你已走完从机器学习到大模型的完整路径。`,
  },
  quiz: [
    {
      question_id: 'ft_q1', question_type: 'single',
      question_text: 'LoRA 高效微调的核心思想是？',
      options: [
        { option_id: 'a', option_text: '冻结原权重，只训练注入的低秩旁路矩阵' },
        { option_id: 'b', option_text: '重新训练模型的全部参数' },
        { option_id: 'c', option_text: '删除模型的大部分层' },
      ],
      correct_answer: 'a',
      explanation: 'LoRA 冻结原权重 W，旁路注入低秩矩阵 BA 并只训练 A、B，大幅降低可训练参数量。',
    },
    {
      question_id: 'ft_q2', question_type: 'boolean',
      question_text: '相比全量微调，参数高效微调（PEFT）通常显著降低显存与算力开销。',
      options: [{ option_id: 'true', option_text: '正确' }, { option_id: 'false', option_text: '错误' }],
      correct_answer: 'true',
      explanation: 'PEFT 只更新极少量参数，冻结主干，因而显存与算力开销远低于全量微调。',
    },
    {
      question_id: 'ft_q3', question_type: 'multiple',
      question_text: '以下哪些属于参数高效微调方法？（多选）',
      options: [
        { option_id: 'a', option_text: 'LoRA' },
        { option_id: 'b', option_text: 'P-Tuning / Prompt-Tuning' },
        { option_id: 'c', option_text: 'QLoRA' },
        { option_id: 'd', option_text: '从零随机初始化重新训练整个模型' },
      ],
      correct_answer: ['a', 'b', 'c'],
      explanation: 'LoRA、P-Tuning、QLoRA 都是 PEFT；从零重训整个模型属于全量训练，不是高效微调。',
    },
    {
      question_id: 'ft_q4', question_type: 'single',
      question_text: 'QLoRA 相比普通 LoRA 的突出特点是？',
      options: [
        { option_id: 'a', option_text: '结合 4-bit 量化进一步降低显存，使单卡微调大模型成为可能' },
        { option_id: 'b', option_text: '必须使用上百张 GPU' },
        { option_id: 'c', option_text: '不需要任何训练数据' },
      ],
      correct_answer: 'a',
      explanation: 'QLoRA 用 4-bit 量化冻结主干并配合 LoRA，显存进一步下降，单卡即可微调大模型。',
    },
  ],
  mindmap: `# 大模型微调技术
## 为什么微调
### 预训练通才→领域专才
### 全量微调成本高
## 参数高效微调 PEFT
### LoRA 低秩旁路
### P-Tuning 软提示
### QLoRA 量化+LoRA
## 权衡
### 秩 r 与表达力
### 成本/效果
`,
  diagram: `flowchart LR
  PT["预训练大模型 (冻结 W)"] --> ADD["注入低秩旁路 BA"]
  DATA["少量领域数据"] --> ADD
  ADD --> TR["仅训练 A,B"]
  TR --> MERGE["合并 W + BA"]
  MERGE --> USE["领域适配模型 · 推理无额外延迟"]
`,
}

/** 知识点 id → mock 学习内容。nn 不在此表（沿用 LearningResource 内既有精修常量作默认）。 */
export const KP_RESOURCES: Record<string, KpResourceContent> = {
  ml,
  dl,
  cnn,
  transformer,
  finetune,
}

/* ─────────────────── 体系目录点（非核心 72 点）通用模板内容 ─────────────────── */

/**
 * 非核心目录点的 mock 内容包：按知识点名称/简介参数化生成通用讲义骨架 + 导图 + 图解。
 * 仅 mock 模式使用（联调下内容由后端生成引擎按该 kpId 真实按需生成）；避免此前
 * 「未收录 kpId 回退 nn 内容」导致名称与正文不符。分阶测试题库未覆盖非核心点 → quiz 为空，
 * 资源页对空题目有明确的未开放提示。
 */
export function genericKpContent(name: string, description: string): KpResourceContent {
  const desc = description || `${name} 的核心概念与应用`
  const lecture = (tone: string, extra: string) => `# ${name}（${tone}）

> 本讲义由**领域知识生成 Agent**按「${tone}」难度生成（演示模式为模板内容；联调模式将基于知识库真实生成）。

## 一、这个知识点讲什么

${desc}

## 二、学习要点

- 理解 ${name} 要解决的问题与适用场景
- 掌握其核心机制与关键概念
- ${extra}

## 三、如何继续深入

结合右侧先修关系，先补齐前置知识，再通过图解与外部资源建立直观理解。`
  return {
    lectureByLevel: {
      入门: lecture('入门版', '用直观例子建立第一印象，不深究数学细节'),
      初级: lecture('初级版', '能向他人复述其工作流程与输入输出'),
      高级: lecture('高级版', '理解其数学原理、局限与最新进展'),
    },
    quiz: [],
    mindmap: `# ${name}
## 核心概念
### ${desc}
## 关键机制
## 实践应用
## 进阶方向
`,
    diagram: `flowchart LR
  A["先修基础"] --> B["${name}"]
  B --> C["核心机制"]
  B --> D["典型应用"]
  C --> E["实践与进阶"]
  D --> E
`,
    code: genericCodeDemo(name, desc),
    video: genericVideoScript(name, desc),
  }
}

/**
 * 非核心目录点的通用讲解视频分镜：按名称/简介参数化，首幕即明示为通用讲解模板
 * （演示模式；联调走 8.3 按知识点真实生成分镜）。
 */
export function genericVideoScript(name: string, description: string): KpVideoScript {
  const desc = description || `${name} 的核心概念与应用`
  return {
    title: name,
    scenes: [
      {
        title: `课程导入 · ${name}`,
        points: [desc, '通用讲解模板（演示模式）', '联调后按知识点真实生成'],
        narration: `欢迎学习${name}。本视频为通用讲解模板，联调模式将按该知识点真实生成分镜。`,
      },
      {
        title: '它要解决什么问题',
        points: ['明确适用场景与目标', '梳理输入与输出', '定位与先修知识的关系'],
        narration: `先想清楚${name}要解决什么问题、适用在什么场景，再进入机制细节。`,
      },
      {
        title: '核心机制',
        points: ['抓住关键概念与术语', '走一遍工作流程', desc],
        narration: `围绕核心机制，抓住关键概念，把${name}的工作流程完整走一遍。`,
      },
      {
        title: '实践与进阶',
        points: ['了解典型应用与局限', '避开常见误区', '结合图解与外部资源深入'],
        narration: `最后了解${name}的典型应用与局限，结合知识图解与外部资源持续深入。`,
      },
    ],
  }
}

/**
 * 讲解视频 mock 分镜解析：kpId → 该知识点分镜脚本。
 * nn 与未知 kpId 返回 null——由 VideoLecture 回落其内置 DEFAULT_SCENES（与后端 nn 脚本逐字一致，零回归），
 * 避免本数据模块反向依赖 remotion 运行时。
 */
export function kpVideoScript(kpId: string): KpVideoScript | null {
  if (kpId === 'nn') return null
  const hit = KP_RESOURCES[kpId]
  if (hit) return hit.video
  const ks = ksPointById(kpId)
  if (ks) return genericVideoScript(ks.name, ks.description)
  return null
}

/**
 * 非核心目录点的通用代码实操 demo：按名称/简介参数化，且**明示是通用示例**
 * （不假装为该知识点定制；联调模式下代码资源目前无后端生成端点，故 mock/联调同用本模板）。
 */
export function genericCodeDemo(name: string, description: string): KpCodeDemo {
  const kpLiteral = JSON.stringify({ name, description })
  return {
    hint: `「${name}」暂无定制实操，以下为通用示例——编辑 kp 卡片数据（要点/自测清单），右侧实时渲染：`,
    js: `// 【通用示例】"${name}" 暂无定制代码实操；下面用一段可编辑的 JS 渲染该知识点的学习卡片。
var kp = ${kpLiteral};
kp.keyPoints = [
  '它要解决的核心问题是什么',
  '关键机制与工作流程',
  '典型应用场景与局限',
];
kp.selfCheck = [
  '我能用一句话讲清它是什么吗？',
  '我能举出一个实际例子吗？',
  '它和先修知识点是什么关系？',
];

var points = kp.keyPoints.map(function (p) { return '<li>' + p + '</li>'; }).join('');
var checks = kp.selfCheck.map(function (c) { return '<li>☐ ' + c + '</li>'; }).join('');

document.getElementById('app').innerHTML =
  '<div style="border:1px solid rgba(127,127,127,.35);border-radius:10px;padding:14px;max-width:520px">' +
    '<div style="font-size:11px;opacity:.55;margin-bottom:6px">通用示例 · 非定制内容</div>' +
    '<h3 style="margin:0 0 6px">📚 ' + kp.name + '</h3>' +
    '<p style="font-size:13px;opacity:.8;margin:0 0 10px">' + (kp.description || '') + '</p>' +
    '<b style="font-size:13px">学习要点</b><ul style="margin:4px 0 10px;font-size:13px">' + points + '</ul>' +
    '<b style="font-size:13px">自测清单</b><ul style="margin:4px 0 0;font-size:13px;list-style:none;padding-left:4px">' + checks + '</ul>' +
  '</div>';
`,
  }
}

/**
 * 代码实操 demo 解析：kpId → 该知识点的可运行 demo。
 * 6 核心点走精修 demo（nn 不在 KP_RESOURCES，此处单独返回）；
 * 体系目录点走通用模板（明示通用示例）；未知 kpId 兜底 nn（与资源页 kpId 兜底口径一致）。
 */
export function kpCodeDemo(kpId: string): KpCodeDemo {
  if (kpId === 'nn') return NN_CODE
  const hit = KP_RESOURCES[kpId]
  if (hit) return hit.code
  const ks = ksPointById(kpId)
  if (ks) return genericCodeDemo(ks.name, ks.description)
  return NN_CODE
}
