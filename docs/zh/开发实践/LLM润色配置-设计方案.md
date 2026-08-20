# 配置 .env + LLM 润色 — 详细设计方案

> **目标**：配置项目根 `.env`（含 API Key），让行业/宏观/公司研报在生成时走 LLM 润色，替代当前 100% 规则模板段，提升决赛报告文字质量。
> **依据**：`docs/zh/开发实践/CCF-BDCI-2026-JiuwenSwarm-差距评审.md` 中「报告文字质量」差距；远程实现 `17804a4` 已就位 LLM 调用链，仅缺 Key 配置。
> **当前现状（实测）**：行业研报 7/7 章为「规则模板段」，宏观研报 6/6 章为模板段，`run_stats.json` 最近 3 次运行 `LLM calls=0`。

---

## 一、现状诊断（已确认的事实）

### 1.1 `.env` 读取链路已就位

```
llm_client.py load_env_file()
  → 路径 = os.path.dirname(__file__) + [".."]*7 + ".env"
  → 实测解析 = E:\git\jiuwenswarm-develop\.env  ← 项目根
  → 当前不存在（os.path.exists=False）
```

### 1.2 LLM 调用链已就位

```
report_writer.py
  ├─ _get_llm()          # L585：懒加载 LLMClient，available() 无 Key 返回 None
  ├─ _build_outline()    # L193：LLM 生成大纲 → 降级固定模板
  ├─ _write_section()    # L228：LLM 逐段撰写 → 降级 _template_section
  └─ _write_industry / _write_macro   # 复用 _write_section
```

**关键点**：代码**不需要改**。只要 `.env` 有 Key，`LLMClient.available()` 返回 True，`_write_section` 自动走 LLM 分支；无 Key 则降级模板段。**配置 `.env` 是唯一动作**。

### 1.3 配置文件读取优先级

```
LLMClient.__init__ → pick("API_BASE","LLM_API_BASE", default=...)
  → 优先级：config > 环境变量 > .env > 默认值
```

| 键 | 默认值 | 用途 |
|----|--------|------|
| `API_BASE` | `https://api.minimaxi.com/anthropic` | MiniMax Anthropic 协议端点 |
| `API_KEY` / `ANTHROPIC_API_KEY` / `MINIMAX_API_KEY` | 无 | 认证 |
| `MODEL_NAME` | `MiniMax-M2` | 模型名 |
| `ZHIPU_API_KEY` | 无 | RAG embedding-3 向量化（可选） |

---

## 二、环境前提（需用户提供）

### 2.1 必需的 Key

| Key | 来源 | 是否必须 | 用途 |
|-----|------|---------|------|
| `API_KEY`（MiniMax Token Plan） | [MiniMax 开放平台](https://platform.minimaxi.com) | **必须** | 报告撰写/大纲 LLM 调用 |
| `ZHIPU_API_KEY` | [智谱 AI 开放平台](https://open.bigmodel.cn) | 可选 | RAG 向量化（无则降级本地 TF-IDF） |

### 2.2 网络前提

- **MiniMax（api.minimaxi.com）**：境内直连即可，`llm_client.py` 已 `trust_env=False` 绕过系统代理 ✅
- **智谱（open.bigmodel.cn）**：境内直连 ✅
- 无需 GitHub 代理（7897）——那是 git 专用

---

## 三、`.env` 文件内容（模板）

在 `E:\git\jiuwenswarm-develop\.env` 创建（**已被 .gitignore 忽略，不会提交**）：

```bash
# ===== finance-report LLM 配置 =====
# MiniMax Token Plan（Anthropic 协议），境内直连
API_BASE=https://api.minimaxi.com/anthropic
API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx    # ← 替换为你的 MiniMax Key
MODEL_NAME=MiniMax-M2

# 可选：智谱 embedding（RAG 向量化主路径，缺失时自动降级本地 TF-IDF）
# ZHIPU_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# 可选：兜底环境变量名（与代码 pick() 兼容）
# ANTHROPIC_API_KEY=...
# MINIMAX_API_KEY=...
```

---

## 四、验证步骤（写报告前先验证 LLM 可用）

### 4.1 一键验证脚本（放技能目录，可复用）

```bash
# 进入技能目录
cd jiuwenswarm/resources/agent/workspace/skills/finance-report

# 验证 LLMClient 能否读到 .env 并成功调用
python -c "
from common.llm_client import LLMClient
client = LLMClient()
print('API_BASE:', client.api_base)
print('MODEL:', client.model)
print('Key 已配置:', bool(client.api_key))
print('available():', LLMClient.available())
if client.api_key:
    resp = client.chat('回复OK两个字', max_tokens=10)
    print('LLM 响应:', repr(resp))
"
```

**预期输出**：
```
API_BASE: https://api.minimaxi.com/anthropic
MODEL: MiniMax-M2
Key 已配置: True
available(): True
LLM 响应: 'OK'
```

### 4.2 若 `available()` 仍为 False

排查顺序：
1. `.env` 是否在项目根（`E:\git\jiuwenswarm-develop\.env`），不是技能目录
2. `API_KEY` 是否有值（`grep API_KEY .env` 应看到非空）
3. 键名是否匹配（代码读 `API_KEY` / `ANTHROPIC_API_KEY` / `MINIMAX_API_KEY`）
4. 环境变量是否覆盖（`env | grep API_KEY` 若存在会优先于 .env）

---

## 五、重跑方案

### 5.1 先单报告验证（快，2 分钟）

```bash
cd jiuwenswarm/resources/agent/workspace/skills/finance-report

# 重跑行业研报（看 run_stats 的 LLM calls 是否 > 0）
python run_report.py industry --name 消费板块 --save

# 重跑宏观研报
python run_report.py macro --period 2026Q2 --save

# 重跑公司研报示例
python run_report.py company --target 600519 --name 贵州茅台 --save
```

**验证标准**：
```bash
# 1. run_stats.json 最近一次运行 LLM calls > 0
python -c "
import json
d = json.load(open('reports/finance-report/decision_log/run_stats.json', encoding='utf-8'))
r = d['runs'][-1]
print('LLM calls:', r['llm']['calls'], 'in:', r['llm']['input_tokens'], 'out:', r['llm']['output_tokens'])
"
# 2. 报告无「规则模板段」标记
grep -c "规则模板段" reports/finance-report/行业研报/industry_消费板块.md
# 应为 0
# 3. 报告仍是 8/7 章齐全
grep -c "^## " reports/finance-report/行业研报/industry_消费板块.md  # 应为 8
```

### 5.2 全池重跑（可选，30-60 分钟，取决于网络与模型速率）

```bash
# 项目根执行一键复现（会重采缺失缓存 + 全池分析 + 决策）
python jiuwenswarm/resources/agent/workspace/skills/finance-report/scripts/reproduce.py

# 若仅需报告润色（决策已定，不重跑采集/分析）：
# 逐个对入选标的补生成研报
for code in 600519 000858 603986 601168; do
  python run_report.py company --target $code --name "$(grep $code example/上市公司列表.xlsx ...)" --save
done
```

---

## 六、成本估算

### 6.1 单份报告 token 消耗（实测模型）

| 报告类型 | 大纲 | 段落（8 章 × 1 次） | 合计（约） |
|---------|------|---------------------|-----------|
| 公司研报 | ~500 in + ~200 out | 8 × (400 in + 300 out) | ~3.7K in + ~2.6K out |
| 行业研报 | 固定模板（不走 LLM 大纲） | 8 × (400 in + 300 out) | ~3.2K in + ~2.4K out |
| 宏观研报 | 固定模板 | 7 × (400 in + 300 out) | ~2.8K in + ~2.1K out |

> 说明：行业/宏观研报的 `_write_industry`/`_write_macro` 大纲**固定模板**（不走 LLM 大纲，见 `report_writer.py:610-612`），只有**段落**走 LLM。公司研报大纲+段落都走。

### 6.2 MiniMax Token Plan 成本

以 MiniMax-M2 单价约 **¥0.005/1K input + ¥0.02/1K output**（Token Plan 订阅价，具体以官方为准）估算：

| 场景 | token 量 | 估算成本 |
|------|---------|---------|
| 1 份公司研报 | 6.3K tokens | ~¥0.1 |
| 1 份行业研报 | 5.6K tokens | ~¥0.09 |
| 全池 8 只研报 + 行业 + 宏观 | ~50K tokens | **~¥0.8** |
| 每日 3 次提交上限 | ~150K tokens | **~¥2.4/天** |

> 结论：**成本可忽略**，即使全量重跑 + 每日 3 次提交，单日成本 < ¥5。

### 6.3 遥测留痕

`run_stats.json` 会自动记录每次 `--save` 运行的 `llm.calls / input_tokens / output_tokens`——这正好满足赛题「资源消耗数据可复现」要求（有真实 LLM 消耗记录反而加分）。

---

## 七、方案选型（为何用 MiniMax 而非其他）

| 方案 | 优点 | 缺点 | 结论 |
|------|------|------|------|
| **MiniMax（默认）** | 代码默认支持；境内直连；Trust_env=False 已配 | 需 Token Plan 订阅 | ✅ **首选**（零代码改动） |
| Anthropic Claude | 质量高 | 需要海外网络/代理；`trust_env=False` 会直连失败 | 需改代码 |
| 智谱 GLM | 境内直连 | 非 Anthropic 协议，需改 `llm_client.py` | 需改代码 |

**结论**：默认走 MiniMax，**零代码改动**，只需在 `.env` 配 Key。若你已有其他 provider 的 Key，我可以评估是否需改 `llm_client.py`。

---

## 八、风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| **R1** MiniMax Key 无效/过期 | LLM 调用 401 | 验证脚本报错 → 提示换 Key；代码会降级模板段（不中断） |
| **R2** MiniMax 接口限流/超时 | 段落生成慢 | `_write_section` 已包 `except` → 降级模板段；超时可调 `timeout`（`llm_client.py:60`） |
| **R3** LLM 生成报告编造数据 | 违反赛题「数据可溯源」 | `_write_section` prompt 已含「数字必须与材料一致，禁止编造」+ `_normalize_section` 归一化 |
| **R4** LLM 生成报告缺「数据来源」标注 | 引用率 < 90% 被 Reviewer 拦 | `_write_section` prompt 强制「段末换行加数据来源」+ Reviewer 复核 |
| **R5** 全池重跑很慢 | 浪费时间 | 建议先单报告验证，确认质量后再决定是否全池 |
| **R6** `.env` 被提交到 git | 泄露 Key | `.gitignore` 已忽略（`L40`），提交前 `git status` 确认 `.env` 不在列表 |
| **R7** 环境变量覆盖 .env | 配了 .env 但无效 | `env` 中已有 `API_KEY` 会优先；验证脚本会打印 `Key 已配置: True/False` |

---

## 九、验收清单

执行完所有步骤后，逐项打勾：

- [ ] `.env` 已创建于 `E:\git\jiuwenswarm-develop\.env`，含有效 `API_KEY`
- [ ] 验证脚本输出 `available(): True` + LLM 响应 OK
- [ ] 重跑行业研报，`run_stats.json` 最近一次 `LLM calls > 0`
- [ ] 行业研报无「规则模板段」标记，仍 8 章齐全，引用率 ≥ 90%
- [ ] 重跑宏观研报，同上（7 章）
- [ ] 重跑公司研报示例（600519），同上
- [ ] `.env` 未被 `git status` 列出（已被忽略）
- [ ] 成本可控（估算 < ¥5/天）
- [ ] 备份降级路径：删掉 `.env` 或 Key 失效时，仍能产出结构完整报告

---

## 十、回滚方案

若 LLM 润色后质量反而下降（或成本超预期），**回滚只需删除/注释 `.env` 的 Key**：

```bash
# 移除 Key（注释或删除），让 available() 返回 False → 回到规则模板
sed -i 's/^API_KEY=.*/API_KEY=/' .env
# 或直接改名备份
mv .env .env.bak
```

代码无需改动——`_get_llm()` 懒加载在无 Key 时返回 None，`_write_section` 自动走 `_template_section`。这是**纯配置开关**，零代码风险。

---

## 附录：参考文件

- LLM 客户端：`jiuwenswarm/resources/agent/workspace/skills/finance-report/common/llm_client.py`
- 报告撰写：`jiuwenswarm/resources/agent/workspace/skills/finance-report/generators/report_writer.py`
- 一键复现：`jiuwenswarm/resources/agent/workspace/skills/finance-report/scripts/reproduce.py`
- 差距评审：`docs/zh/开发实践/CCF-BDCI-2026-JiuwenSwarm-差距评审.md`
