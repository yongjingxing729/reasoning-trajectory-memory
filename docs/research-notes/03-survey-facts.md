# 03 · 综述关键数字复核（arXiv 原文逐条核对）

> 核对方式：`web_fetch` 抓取 arXiv 摘要页（abs）与（必要时）HTML 全文（html v2/v3），逐条给出原文表述与准确数字。
> 抓取时间：2026-11-02。全部 5 个 arXiv ID 均存在且抓取成功（HTTP 200）。
> 结论速览：**综述五条数字全部属实**；其中 4 条的原文表述需补上"相对/up to/over/平均"等限定词，EvolveR 的"三阶段"以正文 §3 为准（摘要措辞是"两阶段 + RL 闭环"）。

---

## 1. Tree-of-Experience：Game of 24 +31.4%、FinEvolveBench tsIC +41.24%

- 文献：arXiv:2608.09044（cs.CL），*Tree-of-Experience: Hierarchical Experience Management for Self-Evolving Agents*，Zihao Deng 等，v1 2026-08-10，v2 2026-08-21。
- 来源：摘要页 https://arxiv.org/abs/2608.09044（v2 摘要）。
- **原文（Abstract，v2）**：
  > "On Game of 24, ToE achieves a **31.4% relative improvement in accuracy** over the experience-free ToT baseline. On FinEvolveBench, ToE **improves tsIC by an average of 41.24%** over the experience-free pipeline **across 12 evaluation settings**, whereas conventional experience-management methods often underperform experience-free baselines."
- 复核结论：
  - ✅ Game of 24：+31.4% **相对**提升（relative improvement），基线是 **experience-free ToT baseline**，不是绝对准确率差。
  - ✅ FinEvolveBench：tsIC 平均 +41.24%，范围限定 **12 个评测设置**，基线是 **experience-free pipeline**。
  - ⚠️ 引用时必须带上 "relative" 与 "average ... across 12 evaluation settings" 两个限定；另可引用同句的负结果（"conventional experience-management methods often underperform experience-free baselines"）作为综述中"经验管理方法并非稳赢"论点的原文支撑。

## 2. ProactAgent：成功率相对提升最高 32%、交互轮次减少 33%

- 文献：arXiv:2604.20572（cs.CL），*Ask Only When Needed: Proactive Retrieval from Memory and Skills for Experience-Driven Lifelong Agents*，Yuxuan Cai 等，v1 2026-04-22，v2 2026-06-04。
- 来源：摘要页 https://arxiv.org/abs/2604.20572（v2 摘要）。
- **原文（Abstract，v2）**：
  > "Experiments on **SciWorld, AlfWorld, and StuLife** show that ProactAgent consistently outperforms all baselines, achieving **up to 32% relative improvement in success rate** and **over 33% reduction in interaction rounds**."
- 复核结论：
  - ✅ "成功率相对提升最高 32%" —— 原文 "**up to** 32% **relative** improvement in success rate"。
  - ✅ "交互轮次减少 33%" —— 原文 "**over** 33% reduction in interaction rounds"（"over" 意为超过 33%）。
  - ⚠️ 两个数字都是"up to / over"（上限/下界表述），且评测环境是 SciWorld、AlfWorld、StuLife 三个基准，引用时建议带环境名。
  - 摘要中与综述路径 A 相关的机制表述（可引用）：ExpOnEvo "organizing past interactions into **factual, episodic, and skill repositories**"；ProactRL "treats retrieval as an explicit policy action ... comparing paired continuations from identical interaction prefixes with and without retrieval ... provides **step-level process rewards**"。

## 3. MemWeaver：上下文压缩 >95%

- 文献：arXiv:2601.18204（cs.CL），*MemWeaver: Weaving Hybrid Memories for Traceable Long-Horizon Agentic Reasoning*，Juexiang Ye 等，v1 2026-01-26（无修订版）。
- 来源：摘要页 https://arxiv.org/abs/2601.18204。
- **原文（Abstract）**：
  > "Experiments on the **LoCoMo benchmark** demonstrate that MemWeaver substantially improves multi-hop and temporal reasoning accuracy while **reducing input context length by over 95%** compared to long-context baselines."
- 复核结论：
  - ✅ "上下文压缩 >95%" —— 原文 "reducing **input context length** by **over 95%** compared to **long-context baselines**"。
  - ⚠️ 精确表述是"输入上下文长度减少 >95%"，对比对象是 long-context baselines，评测在 LoCoMo 上。三组件结构（graph memory / experience memory / passage memory）与 "dual-channel retrieval" 亦出自摘要。

## 4. EvolveR：生命周期三阶段

- 文献：arXiv:2510.16079（cs.CL / cs.AI），*EvolveR: Self-Evolving LLM Agents through an Experience-Driven Lifecycle*，Rong Wu 等，v1 2025-10-17，v3 2026-05-16（ICML 2026 accepted）。
- 来源：摘要页 https://arxiv.org/abs/2510.16079（v3 摘要）+ HTML 全文 https://arxiv.org/html/2510.16079v3 §3。
- **摘要原文（v3）**：
  > "This lifecycle comprises **two key stages**: (1) **Offline Self-Distillation**, where the agent's interaction trajectories are synthesized into a structured repository of abstract, reusable strategic principles; (2) **Online Interaction**, where the agent interacts with tasks and actively retrieves distilled principles to guide its decision-making, accumulating a diverse set of behavioral trajectories. **This loop employs a policy reinforcement mechanism** to iteratively update the agent based on its performance."
- **正文 §3 原文（v3，"three core, interconnected components"）**：
  > "our approach is structured around **three core, interconnected components**, as depicted in Figure 2. First, in the **Offline Experience Self-Distillation** phase, the agent's policy parameters are frozen, and it systematically distills raw trajectories into a curated base of strategic principles. Second, during the **Online Interaction** phase, the agent applies this distilled wisdom to guide its deliberative reasoning and action, generating new, high-quality interaction data. Finally, the entire cycle is driven by a **Policy Evolution** mechanism, where the trajectories collected online are used to update the agent's policy parameters via reinforcement learning, thus closing the loop."
- 复核结论：
  - ✅ "生命周期三阶段"以**正文为准成立**：Offline Experience Self-Distillation → Online Interaction → Policy Evolution（RL 闭环）。
  - ⚠️ 摘要措辞是 "two key stages" + "policy reinforcement mechanism"；综述若写"三阶段"，建议注明来源为正文 §3（"three core, interconnected components"），或改写为"两阶段 + RL 闭环"以避免与摘要冲突。
  - 附正文可引用的量化机制：原则格式 = 自然语言描述 + 知识三元组（§3.2.1）；去重 = 嵌入余弦相似度 + 模型二值语义等价判断（式 (1)，阈值 θ_sim）；动态打分 s(p) = (c_succ(p)+1)/(c_use(p)+2)（式 (2)），低于 θ_prune 周期性剪枝；RL 用 GRPO（§3.3，式 (5)），奖励 = w_o·R_outcome + w_f·R_format。

## 5. LoRAHub：CMA-ES 组合、不训练新参数

- 文献：arXiv:2307.13269（cs.CL / cs.AI），*LoraHub: Efficient Cross-Task Generalization via Dynamic LoRA Composition*，Chengsong Huang 等，v1 2023-07-25，v3 2024-08-19（COLM 2024）。
- 来源：摘要页 https://arxiv.org/abs/2307.13269（v3 摘要）+ HTML 全文 https://arxiv.org/html/2307.13269v3 §3.4。
- **摘要原文（v3）**：
  > "LoraHub can fluidly combine multiple LoRA modules, eliminating the need for human expertise and assumptions. Notably, **the composition requires neither additional model parameters nor gradients**."
- **正文 §3.4 原文（v3）**：
  > "we leverage **Shiwa**, a combinatorial optimization approach... In most of the forthcoming experimental setups, we primarily employ the **Covariance Matrix Adaptive Evolution Strategies (CMA-ES)**... In our application, we employ this algorithm to mold the search space for w."
- 复核结论：
  - ✅ "CMA-ES 组合"：正文 §3.4 确认，实现上通过 Shiwa 选择算法，**主要用 CMA-ES** 搜索组合系数 w。
  - ✅ "不训练新参数"：摘要 "requires neither additional model parameters nor gradients"；正文表 1 中 LoraHub 行 "Gradient-based Training: No"；组合是逐元素加权和 m̂=(ΣwᵢAᵢ)(ΣwᵢBᵢ)（式 (1)），基座模型冻结，仅优化标量系数 w，并加 L1 正则（目标 L + α·Σ|wᵢ|）。
  - ⚠️ 综述引用时应表述为"组合本身不引入新参数、不需要梯度（gradient-free）"，而非"完全无优化"——Adapt 阶段仍在 few-shot 例子上用 CMA-ES 迭代搜索系数。

## 附：复核未覆盖事项说明

- 综述其余 25 篇文献的数字本轮未逐一抓取；如需要可用同样方式批量复核（arXiv ID 均在综述参考文献表内）。
- 全部抓取成功，无网络失败记录；所有引用均已留 arXiv 链接与版本号（ToE/ProactAgent/EvolveR/LoRAHub 以最新版为准，MemWeaver 只有 v1）。
