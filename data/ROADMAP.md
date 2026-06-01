# China Video Bot — Master Roadmap & Memory

> **This is the single source of truth for what we're building and why.**
> If a future Claude session reads ONLY this file, it should know exactly what to do next.
> Update the Status checkboxes as phases complete. Never delete the risk warnings.
>
> Last updated: 2026-06-01

---

## 0. North Star（核心目标）

用户原话：**"根据我给的想法和主题，自动找素材生成高质量视频"**

一句话：输入一个 prompt（如"成都火锅，给第一次来中国的人"），系统自动完成
脚本 → 找素材 → 配音 → 字幕 → 剪辑 → 质检 → （可选）模仿指定风格 → 发布，
产出 Instagram Reels + YouTube Shorts 双格式视频。

两个硬性要求：
1. **脚本独立性** — 能在服务器（AWS/Oracle）上脱离 Claude 自主运行，只靠命令行 + prompt
2. **记忆持续性** — 用户给反馈后，规则永久写入文件；关掉对话也不会丢失能力

---

## 1. 诚实的风险清单（永不删除）

这些是"听起来很美但实际可能不兑现"的陷阱。每次设计新功能前先重读这一节。

| # | 陷阱 | 真相 | 对策 |
|---|------|------|------|
| 1 | "验证不匹配就改 query 重搜" | Pexels 库里没有的素材，换 query 也变不出来 | 改为"一次搜索取 top N 候选，Vision 选最好的"，不空转重搜 |
| 2 | "3秒不够就拼2秒凑5秒" | 两段不同素材硬接大概率割裂，不是"一体感" | 优先单段慢放/Ken Burns；拼接仅作最后手段且需同源 |
| 3 | "模仿参考视频的风格" | 色调能描述但需调色才能复制；航拍镜头 Pexels 给啥算啥，强迫不了 | 只提取**可复制**的参数：节奏/字幕样式/Hook/色调倾向 |
| 4 | "再好的编排出好视频" | **真正瓶颈是素材源质量**，编排是容易的部分 | 多源（+Pixabay）、优中选优、接受 stock 局限并标记 |

**铁律：每个 Phase 做完必须生成真实视频并人工查看。没改善就停下调，不往上叠新 Phase。**

---

## 1.5 API 决策（重要 — 成本、模型选择、生成/验证分离原则）

### 核心原则：生成者 ≠ 验证者（不同模型互相把关）
一个模型有固定盲区——生成时看不出的问题，用同一个模型检查时同样看不出。
因此**生成用一个模型，验证用另一个模型**，才能真正独立挑错。

| 角色 | 任务 | 模型 | 为什么 |
|------|------|------|--------|
| **生成** | Director 写脚本 + 视觉 query | **Groq** `llama-3.3-70b-versatile` | 快、免费、文本够好 |
| **验证** | 内容匹配 / QA 抽帧 / 风格分析 | **Gemini** `gemini-2.0-flash` | 独立于生成模型；免费视觉最强（1500次/天） |
| 文本评分 | Critic 给脚本打分 | 可选迁到 Gemini | 同模型评自己的文略偏袒，换模型更客观 |

> 注：视觉验证检查的是**外部产物**（下载的素材、渲染的视频），即使同模型也不算
> "自评"；但换不同模型（Gemini）能消除共享盲区，是更稳的做法。

### 关于"自己验证自己"的澄清（用户提的好问题）
- 内容验证器查的是 Pexels/Pixabay 下载回来的**素材**对不对，不是查模型自己写的字
- QA 查的是 FFmpeg 渲染出的**视频**有没有问题，也不是查模型自己
- 所以验证有意义。但用 Gemini（≠Groq）做验证，独立性更强 → 采用此方案

### 视觉模型质量排名（2026-06 调研）
1. **Gemini 2.0 Flash** — 免费视觉最强，1500次/天，无需信用卡（aistudio.google.com）← 选它
2. Qwen2.5-VL — 中文/亚洲场景识别强，但需阿里云 DashScope（可能要国内手机）
3. Groq Llama 4 Scout — 快但细粒度分析弱
4. **Claude vision（Opus/Sonnet）— 质量最高，但付费**

### 升级路径（追求更好效果时）
- 当前：Groq（生成）+ Gemini（验证），全免费
- **升级项 A**：验证层换 **Anthropic Claude vision**（console.anthropic.com 注册，
  按量付费）—— 视觉分析质量最高，适合频道变现后投入
- **升级项 B**：中国场景识别若 Gemini 不够准，验证层换 Qwen2.5-VL
- 升级只需改验证层的 API 调用，生成层不动 → 模块化，低改动成本

### Anthropic API ≠ Claude.ai 订阅
订阅（Pro/Max）只能网页/App 对话；API 需单独注册并按量付费，订阅不包含。

### 媒体源 + 模型 API 一览（除 Anthropic 外都免费）
| 源 | 用途 | Key 状态 |
|----|------|---------|
| Pexels | 视频 + 图片 | ✅ 有 |
| Unsplash | 图片兜底 | ✅ 有 |
| Pixabay | 视频第二源 | ✅ 有（2026-06 接入） |
| Groq | LLM 文本生成 | ✅ 有 |
| **Gemini** | **视觉验证/QA** | ⚠️ **key 类型不对**——给的是 `AQ.` 开头的 OAuth 临时令牌（配额极低，持续 429）。需要 `AIza` 开头的正式 API key：aistudio.google.com/apikey |
| Kokoro | TTS（本地） | ✅ 无需 key |
| Anthropic Claude | 视觉升级项 | ⬜ 付费，暂不用 |

---

## 2. 当前状态（已完成，勿重做）

### 已建成的模块
| 文件 | 功能 | 状态 |
|------|------|------|
| `agents/director_agent.py` | Groq 脚本+场景规划，CreativeBrief/ScenePlan，critic 循环，读 guidelines | ✅ |
| `agents/media_agent.py` | Pexels视频→Pexels图→Unsplash图 下载，MediaItem | ✅ |
| `agents/voice_agent.py` | Kokoro TTS（本地）+ edge-tts 回退，句级 SRT | ✅ |
| `agents/video_agent.py` | FFmpeg装配，hook card，比例自适应字幕，drawtext | ✅ |
| `agents/qa_agent.py` | 抽帧 + Claude Vision 质检（仅报告，无修复） | ✅ |
| `agents/analytics_agent.py` | YouTube数据采集 + extract_insights | ✅ |
| `agents/learning_log_agent.py` | 决策审计日志，冲突检测 | ✅ |
| `agents/media_analyst_agent.py` | 用户照片文件夹 → Claude Vision → brief | ✅ |
| `orchestrator.py` | run_pipeline + run_pipeline_from_folder | ✅ |
| `data/director_guidelines.json` | 创意规则（v3），Director 每次读 | ✅ |
| `data/learning_log.md` | 人类可读决策日志（3条） | ✅ |

### 当前关键参数（config/settings.py）
- `TARGET_YOUTUBE_SECONDS = 32`，`TARGET_REELS_SECONDS = 20`
- `SLIDE_DURATION = 4.0`（**Phase 1 要废除其在装配时的硬编码用法**）
- `HOOK_CARD_SECONDS = 2.0`
- `FFMPEG_BIN` / `FFPROBE_BIN` 自动取 conda 环境（含 drawtext）
- TTS：Kokoro `af_heart` @ 1.05x，回退 edge-tts AriaNeural

### 已知的当前缺陷（Phase 1-5 要修）
- ❌ Voice 在 Media **之后**生成 → clip 时长硬编码 4s，与旁白不同步
- ❌ 装配只用 SLIDE_DURATION，不看每句话实际时长
- ❌ 下载的 clip 无内容验证（搜"烤鸭"可能得普通食物）
- ❌ 一个 query 只取"分辨率最高"的第一个，无相关性筛选
- ❌ QA 只报告不修复，无每视频自适应重渲染
- ❌ 无命令行入口，无法服务器自主运行
- ❌ 无话题去重，可能重复生成同主题
- ❌ 无风格参考层

---

## 3. 目标架构（分层）

```
入口层      scripts/run.py  --prompt "..." [--style NAME] [--dry-run]
              │
风格层(P5)   StyleAnalyst: 参考视频 → StyleProfile (data/style_profiles/*.json)
              │              [可选，提供则影响下面所有层]
规划层      TopicGuard(去重) → DirectorAgent(Groq)
              │   读: guidelines.json + StyleProfile + insights.json
              │   → CreativeBrief (scenes + narrations + visual_queries)
              │
生产层      ① VoiceAgent  → Kokoro TTS → MP3 + SRT
            ② parse_srt   → 每场景精确时长 [3.2s, 4.1s, ...]
            ③ MediaDirector: 按场景下载 → Vision优中选优 → clip计划
              │   → list[SceneClipPlan]
              │
装配层      assemble_raw(只拼片段) → raw.mp4
            burn_subtitles(独立可重跑, VideoRenderParams) → final.mp4
              │
质检层      QAAgent: 抽帧+Vision → QAReport
            [有StyleProfile时] 额外对比参考视频 → similarity + 差异
            QARemediation: 调本视频参数 → 只重烧字幕(不重做整片)
              │
发布层      PublisherAgent(YouTube) → AnalyticsAgent(3天后)
                                      → extract_insights → guidelines反馈
```

### Agent 职责（最终版，纪律：只新增 1 个有意义 Agent）
| 角色 | 类型 | 说明 |
|------|------|------|
| DirectorAgent | LLM Agent | 已有，Phase 5 加 prompt + StyleProfile 输入 |
| MediaDirector | LLM Agent | 新模块：下载+Vision选素材+剪辑计划（合并原ContentVerifier+ClipPlanner） |
| QAAgent | LLM Agent | 已有，Phase 3 加修复，Phase 5 加风格对比 |
| **StyleAnalyst** | **LLM Agent** | **唯一真正新增**：参考视频→StyleProfile |
| TopicGuard | 纯函数 | 不是Agent，话题去重 |
| parse_srt | 纯函数 | 不是Agent，SRT→时长 |
| 其余 | 已有 | Voice/Publisher/Analytics/LearningLog 不变 |

---

## 4. 实现计划（Phase by Phase）

> 状态标记：⬜ 未开始 / 🔄 进行中 / ✅ 完成
> 每个 Phase 末尾的【验证关卡】必须通过才能进下一个。

### Phase 1 — SRT 驱动时长 ✅（2026-06-01 完成）
**目标：** 修复"画面与字幕错位"这个真实 bug。让每个场景的时长由旁白决定，而非硬编码。

**实现结果：** 新增 `generate_voice_scenes()` 逐场景生成 TTS，返回精确时长。
装配时每个 clip 用对应场景时长（clamp 到 [2,8]s）。验证：8场景时长
`[3.5,4.2,4.5,4.7,4.4,4.2,4.2,4.1]` 不再是死板的 4s，clip[i]==narration[i] 时长，对齐由构造保证。

**改动文件：**
- `orchestrator.py`：把 Voice 移到 Media 之前；新增 `parse_srt_scene_timings()`
- `agents/video_agent.py`：装配接受每场景时长，废除 SLIDE_DURATION 硬编码
- `config/settings.py`：加 `MIN_CLIP_SECONDS=2.0`, `MAX_CLIP_SECONDS=8.0`

**新函数签名：**
```python
# orchestrator.py
def parse_srt_scene_timings(srt_path: str, n_scenes: int) -> list[float]:
    """解析 SRT，按句子分组返回每场景秒数。失败回退 SLIDE_DURATION。"""

# video_agent.py — assemble_video 加可选参数
def assemble_video(video_id, media_items, audio_path, srt_path,
                   hook_text="", scene_durations: list[float] | None = None) -> dict:
    """scene_durations 提供时，每个 clip 用对应时长，不再用 SLIDE_DURATION。"""
```

**新流程顺序（run_pipeline）：**
```
[1] Director → CreativeBrief
[2] Voice/TTS → audio + srt
[2b] parse_srt → scene_durations
[3] Media → download_media(用 scene_durations 作目标时长)
[4] Assemble(scene_durations) → 每场景时长匹配旁白
[5] QA
```

**【验证关卡】** 生成 1 个视频，肉眼确认：字幕是否跟当前画面对得上？画面切换是否在句子边界？

---

### Phase 2 — 素材质量（真正瓶颈）⬜
**目标：** 提升素材相关性。核心改动：**优中选优**，不是空转重搜（见风险#1）。

**改动文件：**
- `agents/media_agent.py`：加 Pixabay 源；改为下载 top 3 候选
- `agents/media_director_agent.py`（**新建**）：Vision 选最匹配的候选

**新数据结构 + 函数：**
```python
# media_director_agent.py
@dataclass
class ClipSegment:
    path: str; kind: str           # "clip"|"photo"
    trim_start: float; trim_end: float

@dataclass
class SceneClipPlan:
    scene_index: int
    segments: list[ClipSegment]    # 通常1段；时长不足时才2段
    total_duration: float
    match_score: int               # Vision评分0-10；0=未评估(无API key)

def plan_scene_media(video_id, scenes, target_durations) -> list[SceneClipPlan]:
    """每场景: 下载top3候选 → Vision选最匹配 → 不足时长则补充/慢放 → 剪辑计划"""

def get_clip_duration(path: str) -> float:
    """ffprobe 返回秒数，出错返回 0"""
```

**诚实边界：** Vision 从**实际下载到的**候选里挑最好的。全都不行 → 用占位图 + 写 todo 文件（遵循用户的 placeholder 偏好）。不假装能创造不存在的素材。

**【验证关卡】** 生成视频，对比 Phase 1：素材是否更贴合旁白？拿一个刁钻主题（如"南京烤鸭"）测试。

---

### Phase 3 — 装配拆分 + QA 自动微调 ✅（2026-06-02 完成）
**实现：** VideoRenderParams（每视频独立）+ 保存 raw 视频 + rerender_subtitles
只重烧字幕 + adjust_params_from_qa 共识调参（large→缩/small→放/位置/滞留）。
验证：Gemini 报"字幕太大"(8/8) → font 0.040→0.034 → 两版本重烧 → 画面平衡。
洞察：同字号在干净背景显大、繁忙背景显小 → 正是每视频自适应的理由。

**目标：** 让 QA 能**只重烧字幕**修复问题，10秒搞定，不重做整片。每视频独立微调。

**改动文件：**
- `agents/video_agent.py`：拆 `assemble_raw()` + `burn_subtitles()`
- `agents/qa_agent.py`：加 `adjust_params_from_qa()`
- `orchestrator.py`：QA 失败 → 调参 → 重烧字幕

**新结构 + 函数：**
```python
# video_agent.py
@dataclass
class VideoRenderParams:               # 每视频独立，不写回 settings
    fontsize_pct: float = 0.032
    subtitle_y: float = 0.82
    max_cue_dur: float = 3.5

def assemble_raw(video_id, clip_plans, w, h, hook_text="") -> str:
    """只拼片段，无音频无字幕，返回 raw_video_path"""

def burn_subtitles(raw_path, audio_path, srt_path, out_path,
                   params: VideoRenderParams) -> None:
    """独立字幕烧录，可重复调用"""

# qa_agent.py
def adjust_params_from_qa(report, current: VideoRenderParams) -> VideoRenderParams:
    """规则调参: 字幕太大→fontsize×0.85; 太低→y-0.05; 滞留→max_cue-0.5"""
```

**关键纪律：** `VideoRenderParams` 只活在本次运行内存里。同类问题出现 3 次 → LearningLog 提示，再由人更新默认值。**不硬编码、不全局污染。**

**【验证关卡】** 故意把默认 fontsize 设超大，跑一遍，确认 QA 抓出"字幕太大"并自动调小重烧。

---

### Phase 4 — CLI + 自主运行 + 话题去重 ⬜
**目标：** 能在服务器上脱离 Claude 运行。实现"脚本独立性"。

**改动文件：**
- `scripts/run.py`（**新建**）：命令行入口
- `agents/director_agent.py`：`create_brief` 加 `prompt=""` 参数
- `orchestrator.py`：加 TopicGuard 去重

**CLI 用法：**
```bash
python scripts/run.py                                    # 每日自动模式
python scripts/run.py --prompt "成都火锅 给新手"          # prompt 驱动
python scripts/run.py --audience newcomer --dry-run
python scripts/run.py --from-folder ~/photos --dry-run   # 用户素材模式
```

**新函数：**
```python
# director_agent.py
def create_brief(audience_type=None, target_seconds=None, prompt="") -> CreativeBrief:
    """prompt 拼进 Groq user message 作创意方向，不改 JSON schema"""

# orchestrator.py (或新 utils)
def guard_topic(proposed_topic: str, days: int = 14) -> bool:
    """查 published_videos.json，14天内有相似主题返回 False"""
```

**【验证关卡】** `python scripts/run.py --prompt "..." --dry-run` 完整跑通出视频。

---

### Phase 5 — 风格参考层 ⬜（仅在 1-4 能出好视频后做）
**目标：** 模仿指定参考视频的风格。只提取**可复制**的参数（见风险#3）。

**改动文件：**
- `agents/style_analyst_agent.py`（**新建**，唯一真正新增的 Agent）
- `agents/qa_agent.py`：加对比模式（不新建 Agent）
- `scripts/run.py`：加 `--learn-style` / `--style` 参数

**新结构 + 函数：**
```python
# style_analyst_agent.py
@dataclass
class StyleProfile:
    name: str; source: str
    avg_clip_seconds: float; total_seconds: float    # 节奏(ffprobe场景检测)
    hook_position: str; hook_style_desc: str          # Hook
    subtitle_position: str; subtitle_size: str
    subtitle_has_box: bool; subtitle_desc: str        # 字幕(Vision)
    color_mood: str; shot_types: list[str]            # 视觉(Vision)
    narration_tone: str; narration_pace: str          # 旁白
    full_description: str

def analyse_style(video_path: str, name: str) -> StyleProfile:
    """本地视频 → ffprobe节奏 + Vision抽帧分析 → 存 data/style_profiles/{name}.json"""

# qa_agent.py 扩展
def compare_to_reference(generated_video, style_profile) -> dict:
    """抽两边帧 Vision对比 → {similarity:0-10, differences:[], adjustments:{}}"""
```

**输入策略：** 先支持**本地文件**（最可靠）。YouTube URL 用 yt-dlp（成熟）。Instagram 不稳定，让用户手动下载后丢文件夹。**先用我们自己 output/ 的视频验证提取+对比机器能工作，再加 URL 下载。**

**【验证关卡】** 分析我们自己已生成的一个视频，看 StyleProfile 是否合理；再让另一个视频跟它对比，看差异判断是否靠谱。

---

### Phase 6 — 自主运行文档 ⬜
**目标：** 写 `data/AUTONOMOUS_GUIDE.md`——系统的"大脑说明书"，脱离 Claude 也能理解和操作。

**内容：**
- 如何在服务器运行（环境、依赖、API keys、cron）
- 每个文件是什么、为什么
- 学习反馈循环如何工作
- 如何给 Bot 反馈（编辑 guidelines + learning_log）
- 当前所有技术标准（从 learning_log entry #3 同步）
- 常见问题排查

**注：** 本 ROADMAP.md 已是 Phase 6 的雏形；AUTONOMOUS_GUIDE 是面向"操作"的精简版。

---

## 5. 学习 / 记忆机制（已建成，持续用）

```
用户反馈 → Claude分析 → 更新 director_guidelines.json (版本号+1)
                      → 写 learning_log.md (原话+推理+预期效果)
                      → 用户审查 learning_log.md (可标记 ❌Rejected)

YouTube数据(3天后) → extract_insights → insights.json
                   → 冲突检测(数据 vs 人工规则) → CONFLICT条目等人裁决

每次生成 → Director 读 guidelines + insights (+StyleProfile)
```

**三个记忆文件：**
- `data/director_guidelines.json` — 创意规则，Director 每次读，影响生成
- `data/insights.json` — 数据提炼的模式，影响选题
- `data/learning_log.md` — 人类审查用，记录所有决策和原因
- `data/ROADMAP.md` — **本文件**，实现计划与未完成事项

---

## 6. Parking Lot（提过但未排期的想法）

- **ElevenLabs 升级**：频道开始变现后，TTS 换 ElevenLabs Starter（$5/月）获得更好情感
- **Kokoro 换声音试验**：af_bella / af_jessica 可能比 af_heart 情感更丰富
- **色调调色**：若要真复制参考视频色调，需在 FFmpeg 加 curves/colorbalance（Phase 5+）
- **字幕逐字卡拉OK动画**：高级字幕样式，需逐词时间轴
- **多语言**：目前仅英文，未来可能加字幕翻译
- **背景音乐**：免费音乐源 + 自动配乐（版权需谨慎）

---

## 7. 待用户提供 / 确认

- [x] ~~`ANTHROPIC_API_KEY`~~ 不用了 → 改用 Groq Vision（免费）。详见 §1.5
- [ ] YouTube 频道创建（`yuu.chenn.zzz@gmail.com` 当前无频道，上传会失败）
- [ ] Oracle Cloud VM（信用卡验证卡住中）
- [ ] 确认视频时长偏好（当前 32s YouTube / 20s Reels）
- [ ] 风格参考视频（Phase 5 时提供本地文件或 YouTube URL）

---

## 8. 进度追踪

- [x] 基础流程（Director→Media→Voice→Video→QA→Publish）
- [x] Kokoro TTS + hook card + 比例字幕
- [x] 学习日志 + guidelines 系统
- [x] 用户素材模式（media_analyst）
- [x] **Phase 1 — SRT 驱动时长** ✅
- [x] **字幕修复批次** ✅（sync修复/一场景一字幕/每行居中/Anton字体/Director旁白长度）
- [~] **Phase 2 — 素材质量** 🔄（已做：跨场景去重✅ + Pixabay 第二源✅ +
      Gemini 视觉选材&QA 代码✅；阻塞：Gemini key 类型不对需换正式 AIza key。
      优雅降级已验证——无 Gemini 时靠去重仍出好视频）
- [x] **Phase 3 — 装配拆分 + QA 自动微调** ✅
- [x] **Phase 4 — CLI + 自主运行 + 话题去重** ✅
- [x] **Phase 5 — 风格参考层** ✅
- [x] **Phase 6 — AUTONOMOUS_GUIDE.md** ✅
