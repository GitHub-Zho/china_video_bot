# China Video Bot — Pipeline Flow & Optimization Notes

> 临时工作文档，记录完整流程、已知问题、优化方向。

---

## 一、完整生成流程（每次 `--type both` 跑两遍）

```
用户输入 prompt
      │
      ▼
[1] Director (Qwen-text, qwen-max)
      ├─ 加载 director_guidelines.json (v8) — 根据 growth/info 注入对应格式规则
      ├─ 生成 5 场景 Brief (narration / search_query / gen_prompt / topic_label)
      ├─ Critic (Qwen-text) 打分 ≥7/10 才通过，否则最多重试 3 次
      └─ 输出 brief.json
          │
      [1b] Art Direction — ★优化点A 已完成，合并入 Director
          └─ Director 直接在 JSON schema 中输出 gen_prompt（省去独立 Qwen 调用）
                │
      ▼
[2] Voice (Kokoro TTS, 本地)
      ├─ 每场景单独合成音频 + 0.4s 前置静音（字幕提前）
      ├─ 场景间 0.6s 尾音 / MIN_SCENE_SECONDS=3.2s
      └─ 输出 audio.mp3 + subtitles.srt + scene_durations[]
          │
      ▼
[3] Media — 每场景并行 ★优化点B（目前串行）
      ├─ 可选：reference_agent 提前抽取 B站/YouTube 参考帧
      │       yt-dlp 下载 12s 片段 → FFmpeg 抽帧 + drawbox 遮水印
      │       同一视频重复调用时应命中本地缓存 ★优化点C（已修缮待实现）
      │
      └─ compete_and_apply（每场景）
            ├─ 搜索：Pexels + Pixabay（关键词 search_query）→ 最多 5 个候选预览图
            ├─ 生成：Wanxiang t2i（gen_prompt，异步提交 → 轮询）★优化点B：可批量提交
            ├─ 参考帧（若有）：真实视频帧，已用过的不重复入池
            └─ 评委：Qwen-VL（qwen-vl-max）对所有候选打分 0-10
                  ├─ 胜者 ≥5 分 → 写入 media/{i:02d}.jpg 或 .mp4
                  └─ 无胜者 → 回退：第一个 Pexels 视频（不评分）
          │
      ▼
[4] Video Assembly (FFmpeg)
      ├─ 每场景：photo → _make_clip_from_photo (Ken Burns zoompan)
      │                   clip  → _make_clip_from_video (scale-crop)
      │         ★ 当前 BUG：zoompan 直接 s=WxH，横图→竖框会拉伸变形
      │         ★ 修复：先 scale 到覆盖高度，再从中心 crop 竖条
      │
      ├─ 拼接所有场景片段 → concat
      ├─ 叠加音频 (audio.mp3)
      ├─ 烧录字幕 (drawtext / libass，Anton 字体)
      ├─ 左上角 topic badge (drawtext)
      ├─ Hook card 片头（2s）
      ├─ 淡入淡出（每场景 0.3s）
      └─ 输出两份：
            youtube.mp4  (1920×1080, 16:9)
            reels.mp4    (1080×1920, 9:16)  ← 现在变形，需修复
          │
      ▼
[5] QC (本地规则检查)
      ├─ 时长合规 (≈目标秒数 ±2s)
      ├─ 文件大小 / codec 检查
      └─ 若不合规 → 警告（不阻断）
          │
      ▼
[6] QA (Qwen-VL, qwen-vl-max) — 最重要的质量门
      ├─ 从视频密集采样（每 1.5s 一帧，最多 20 帧）
      ├─ 每帧打上对应场景 narration 标签
      ├─ 用 topic 注入上下文（"烤鸭皮蘸糖" = 白糖传统，而非乱蘸）
      ├─ 检测：内容不匹配 / 字幕问题 / 视觉问题
      └─ 发现内容不匹配 → 自动修复：
              find_replacement_clip（stock + AI image + AI video 竞争）
              ★ 同样传入 reference_frames，保证真实帧仍可用
              替换成功 → _reassemble_from_media（重组整个视频）
              替换失败 → download_scene_alternatives 供手动 --fix 挑选
          │
      ▼
[7] 发布 (YouTube Data API)
      └─ 仅在非 --dry-run 时执行
```

---

## 二、当前已知问题

| # | 问题 | 位置 | 状态 |
|---|------|------|------|
| 1 | Reels 横图竖框拉伸变形 | `video_agent._make_clip_from_photo` | ✅ 已修（center-crop 竖条 + zoompan） |
| 2 | 参考帧每次重下（无缓存） | `reference_agent.extract_reference_frames` | ✅ 已修（MD5 缓存，0.0s 命中） |
| 3 | Wanxiang 每场景串行提交 | `compete_and_apply` | ⚠️ 可优化 |
| 4 | Director + Art Director 两次独立 Qwen 调用 | `orchestrator._run_one` | ✅ 已合并（gen_prompt 嵌入 Director JSON，省一次 RTT） |

---

## 三、优化机会分析

### A. Director + Art Director 合并 ✅ 已完成
**实现**：在 `director_agent._SYSTEM` 加入 ART DIRECTION 规则块；场景 schema 新增 `gen_prompt` 字段；`orchestrator._run_one` 移除 `enrich_gen_prompts` 调用。  
**收益**：省去一整轮 Qwen 调用（约 800-1200 token 输入 + 1 次 RTT，每个视频节省 ~5-10s）。  
**质量保障**：Director 拿到完整 topic 上下文再写 gen_prompt，效果与 Art Director 独立调用等价。

### B. Wanxiang 批量并行提交（中优先级）
**现状**：5 个场景串行提交 → 每个等待 ~30s → 总计 ~150s。  
**改为**：先批量提交全部任务（5 个 task_id），然后并发轮询，全部完成后统一打分。  
**收益**：生成时间从 ~150s → ~35s（约 4× 加速）。  
**注意**：Wanxiang 有并发限制（免费档 1-3 并发），需限流。

### C. 参考帧本地缓存（已规划）
**现状**：同一 URL 每次运行都重新下载 12s 片段。  
**改为**：按 `hash(url + timestamp)` 命名缓存文件存到 `output/ref_cache/`，命中则跳过下载。  
**收益**：迭代调试时节省 yt-dlp 调用和 CDN 带宽。

### D. QA 提前内容预检 ✅ 已完成
**实现**：`media_agent.pre_check_and_fix_media()` — 在 assembly 前对所有场景媒体一次 Qwen-VL 批量评分（score<6 立即用 compete_and_apply + make_video=True 重选），`orchestrator._build_from_brief` 在步骤 [3b] 调用。  
**收益**：省去 1 次完整视频组装（约 15-40s FFmpeg + 1 次 QA Qwen-VL 调用）。QA 步骤仍保留，但处理的是字幕/视觉问题，内容问题已在 pre-check 消化。

### E. 横/竖屏素材分路采集（长期）
**现状**：AI 生成图和库存素材都是 16:9，Reels 竖版靠裁切/缩放。  
**改为（理想）**：Wanxiang 支持竖版尺寸 `720*1280`，对 Reels 路径直接生成竖版图，省去裁切。  
**改为（近期）**：在组装 Reels 时自动 smart-crop（见下方修复方案）。

---

## 四、Reels 变形修复方案

### 现状根因
`_make_clip_from_photo` 里 `zoompan` 的 `s=1080x1920`（9:16）直接作用于 `scale=8000:-1` 的横图（宽>>高），zoompan 会强行输出目标尺寸导致内容被水平压缩。

### 修复方案：先竖向裁内容，再 zoompan
对横图（16:9）→ 竖框（9:16）的处理步骤：
1. `scale=-1:8000`（按高度放大，保持宽高比）
2. `crop=w=ih*9/16:h=ih:x=(iw-ih*9/16)/2:y=0`（从中心取竖条）
3. `zoompan` 在竖条上做 Ken Burns
4. `scale=1080:1920`（输出目标尺寸）

对横图（16:9）→ 横框（16:9，YouTube）不变，流程照旧。

对竖图（如已经是 9:16 的参考帧）→ 竖框：不需裁切，直接 zoompan。

```python
def _portrait_vf(w, h):
    """返回适合竖屏输出的 vf 字符串（从景观图智能中心裁竖条）"""
    if w < h:  # 源已经是竖的，直接缩放
        return f"scale={w}:{h},setsar=1"
    # 源是横的：按高度放大后从中心裁竖条
    return (
        f"scale=-1:8000,"
        f"crop=w=ih*{w}/{h}:h=ih:x=(iw-ih*{w}/{h})/2:y=0,"
        f"scale={w}:{h},setsar=1"
    )
```

---

*文档最后更新：2026-06-10*
