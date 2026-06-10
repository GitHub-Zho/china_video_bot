# Director Learning Log

Every entry records a change to the Director Agent's knowledge.
Review this file to audit, question, or override any decision.
Mark a conflict's status as `❌ Rejected` or `⚠️ Review needed` to flag it.

---

## [1] 2026-05-30 · GUIDELINE — v0 → v1: Initial guidelines — established from first dry-run review. 

**Source:** Human feedback → Claude analysis → director_guidelines.json

**Analysis:**
Feedback received: Initial guidelines — established from first dry-run review. Narrations were too generic (3-5 words, no facts, filler phrases). Upgraded to require 10-15 word sentences with numerical facts and second-person tone.

**Action taken:**
  [Added DO rule] Include at least one specific numerical fact per video (years old, km/h, metres tall, cost in USD)
  [Added DO rule] Name specific sub-locations, not just cities — e.g. Nanluogu Xiang alley not just Beijing
  [Added DO rule] Open scene 0 with a contrast or surprise that a Western viewer would find counter-intuitive
  [Added DO rule] Use second-person (you) to make the viewer feel they are already there
  [Added DO rule] Each narration should answer: why would THIS specific detail surprise a Western viewer?
  [Added AVOID rule] Generic filler phrases: explore the unknown, vibrant city, breathtaking scenery, must-visit destination
  [Added AVOID rule] Sentences that could apply to any country — every line must be China-specific
  [Added AVOID rule] Starting scenes with The — sounds like a documentary, not a reel
  [Added AVOID rule] Stacking adjectives without facts: stunning, beautiful, incredible with no supporting detail
  [Added AVOID rule] Ending sentences with always, every time, completely — unnatural filler words
  [Added style note] Narration tone: a well-travelled friend texting you tips, NOT a BBC documentary narrator
  [Added style note] Sentences should feel unfinished enough that viewers want to watch the next scene
  [Added style note] Contrast works best: ancient vs modern, cheap vs expected luxury, hidden vs famous
  [Added good example] This 1,200-year-old town costs less than ten dollars a night to stay in.
  [Added good example] The bullet train from Beijing to Shanghai takes four hours — a flight takes the same.
  [Added good example] Locals here eat breakfast standing up in an alley that has not changed since the Tang dynasty.
  [Added bad example] Explore the unknown.
  [Added bad example] Visit ancient villages.
  [Added bad example] Experience breathtaking local culture every time.
  Guidelines version: v0 → v1

**Expected effect:**
Director Groq prompt will include updated rules on next run. Critic scores for specificity/reels_fit/word_count should reflect the change.

**Conflicts with existing rules:**
None detected.

**Status:** ✅ Applied

---

## [2] 2026-05-31 · GUIDELINE — v1 → v2: User watched first two generated videos. Feedback: (1) conte

**Source:** Human feedback → Claude analysis → director_guidelines.json

**Analysis:**
Feedback received: User watched first two generated videos. Feedback: (1) content not engaging enough — narrations are factual but not curiosity-driven, no scroll-stopping hook tension; (2) visual queries too generic — just city names, not cinematic shot descriptions; (3) CTA too formulaic ("discover the secrets of this incredible country")

**Action taken:**
  [Added DO rule] Open scene 0 with a QUESTION or unresolved tension, not a statement
  [Added DO rule] Each narration must create curiosity for the NEXT scene — end lines slightly open
  [Added DO rule] Every 3 scenes, introduce a contrast or unexpected twist
  [Added DO rule] Visual queries must include location + time of day + lighting + one emotional detail
  [Added DO rule] Visual queries must be cinematic — describe a shot to a film director, not a search engine
  [Added AVOID rule] Repeating the same location across multiple scenes
  [Added AVOID rule] Visual queries that are just city names
  [Added AVOID rule] CTA lines with "discover the secrets" — too generic
  [Added style note] Think scroll-stopping: scene 0 must make someone pause their thumb in under 3 seconds
  [Added style note] The CTA should feel like a cliffhanger — hint at something not yet shown
  [Added good example] What if I told you this 1,200-year-old town costs less than ten dollars a night?
  [Added good example] This is China's answer to the Amalfi Coast — except almost nobody outside China knows it exists.
  [Added bad example] Follow for more hidden China adventures and discover the secrets of this incredible country.
  Guidelines version: v1 → v2

**Expected effect:**
Director Groq prompt will include updated rules on next run. Critic scores for specificity/reels_fit/word_count should reflect the change.

**Conflicts with existing rules:**
None detected.

**Status:** ✅ Applied

---

## [3] 2026-06-01 · RECORD — Architecture decisions & user feedback verbatim

**Source:** Multi-session conversation history → Claude synthesis

**Analysis:**
This entry documents all key decisions and user's original feedback so a future Claude
or human reviewer can understand the WHY behind every major parameter — not just what changed.

**User's original feedback (exact intent):**

1. "语音朗读得太像 AI 了，没有情感起伏，也没有语速快慢的变化"
   Voice sounds robotic, no emotional variation, no pacing changes.
   → Switched from edge-tts AriaNeural to Kokoro af_heart (local, free, warmer).
   → ElevenLabs Starter ($5/mo) recommended when channel monetises — best emotional range.

2. "视频不需要到60秒，太长了"
   60 seconds is too long for Reels/Shorts format.
   → TARGET_YOUTUBE_SECONDS 60→32s, SLIDE_DURATION 5→4s, narration 10-15 words → 6-10 words.
   → Research confirms 15-30s optimal for Instagram Reels; 32s works for both platforms.

3. "他们通常会在视频的第一帧加一些文字叙述来吸引注意力"
   Best practice: first frame freeze + bold hook text stops scrolling.
   For faceless channels, bold text overlay works equally well as face-reveal.
   → Added HOOK_CARD_SECONDS=2.0 freeze frame with hook text. Proven +50pct 3-second retention.

4. "如果我给你一些素材（比如一些小吃之类的图片），你能替我整理归纳，根据图片进行分析"
   User wants to provide own photos, get auto-generated matching narration and video.
   → Built media_analyst_agent.py: Claude Vision analyses each image per-image narration.
   → run_pipeline_from_folder(path, dry_run=True) in orchestrator.

5. "内容不够有吸引力 / 照片不够好 / 字幕也没有显示在屏幕"
   Content factual but not curiosity-driven. Visual queries too generic. No subtitles on Mac.
   → Guidelines v2 (curiosity gaps, cinematic queries). Switched to conda FFmpeg (has drawtext).
   → Rewrote subtitle approach: drawtext+enable per cue, unquoted text with escaped chars.

**Key technical decisions:**

- Kokoro over edge-tts: free, local, no rate limits, more natural prosody for travel content.
- drawtext with enable not sendcmd: sendcmd breaks on commas and apostrophes in text.
- FFMPEG_BIN from sys.executable.parent: Homebrew FFmpeg lacks libfreetype; conda-forge has it.
- Hook card is visual-only (silent); subtitle timestamps shifted by HOOK_CARD_SECONDS via _shift_srt().
- Director guidelines in SYSTEM prompt not USER prompt: forces Groq to treat as hard constraints.

**What to try next:**

- Try Kokoro af_bella or af_jessica for more expressive delivery (af_heart is warm but neutral on peaks).
- Hook card font size (7.5pct of frame height) may need tuning for Reels 9:16 aspect ratio.
- When channel monetises: upgrade to ElevenLabs Starter ($5/mo) for better emotional range.

**Action taken:** Documentation entry only.

**Conflicts with existing rules:** None.

**Status:** ✅ Documentation only

---

## [4] 2026-06-02 · ANALYTICS — QA issues in youtube.mp4

**Source:** qa_agent.qa_check() on youtube.mp4

**Analysis:**
Gemini Vision found 8 issue(s):
    ⚠️ [subtitle] t=0.5s — Subtitle text is very large and takes up a significant portion of the frame, obscuring the background scenery.
    ⚠️ [subtitle] t=2.3s — Subtitle text is very large and takes up a significant portion of the frame, obscuring the background scenery.
    ⚠️ [subtitle] t=6.0s — Subtitle text is very large and takes up a significant portion of the frame, obscuring the background scenery.
    ⚠️ [subtitle] t=10.0s — Subtitle text is very large and takes up a significant portion of the frame, obscuring the background scenery.
    ⚠️ [subtitle] t=14.0s — Subtitle text is very large and takes up a significant portion of the frame, obscuring the background scenery.
    ⚠️ [subtitle] t=18.0s — Subtitle text is very large and takes up a significant portion of the frame, obscuring the background scenery.
    ⚠️ [subtitle] t=22.0s — Subtitle text is very large and takes up a significant portion of the frame, obscuring the background scenery.
    ⚠️ [subtitle] t=24.8s — Subtitle text is very large and takes up a significant portion of the frame, obscuring the background scenery.

**Action taken:**
Issues logged. Review and update guidelines if pattern repeats.

**Expected effect:**
If same issue appears in 3+ videos, add a rule to director_guidelines.json.

**Conflicts with existing rules:**
None.

**Status:** ⏳ Monitor — no action taken yet

---

## [5] 2026-06-02 · ANALYTICS — QA issues in youtube.mp4

**Source:** qa_agent.qa_check() on youtube.mp4

**Analysis:**
Gemini Vision found 8 issue(s):
    ⚠️ [subtitle] t=0.5s — Subtitle text is very small and barely visible
    ⚠️ [subtitle] t=2.3s — Subtitle text is very small and barely visible
    ⚠️ [subtitle] t=6.0s — Subtitle text is very small and barely visible
    ⚠️ [subtitle] t=10.0s — Subtitle text is very small and barely visible
    ⚠️ [subtitle] t=14.0s — Subtitle text is very small and barely visible
    ⚠️ [subtitle] t=18.0s — Subtitle text is very small and barely visible
    ⚠️ [subtitle] t=22.0s — Subtitle text is very small and barely visible
    ⚠️ [subtitle] t=25.2s — Subtitle text is very small and barely visible

**Action taken:**
Issues logged. Review and update guidelines if pattern repeats.

**Expected effect:**
If same issue appears in 3+ videos, add a rule to director_guidelines.json.

**Conflicts with existing rules:**
None.

**Status:** ⏳ Monitor — no action taken yet

---

## [6] 2026-06-02 · ANALYTICS — QA issues in youtube.mp4

**Source:** qa_agent.qa_check() on youtube.mp4

**Analysis:**
Gemini Vision found 8 issue(s):
    ⚠️ [subtitle] t=0.5s — Subtitle text is very large and takes up a significant portion of the frame, potentially obscuring important visual content.
    ⚠️ [subtitle] t=2.3s — Subtitle text is very large and takes up a significant portion of the frame, potentially obscuring important visual content.
    ⚠️ [subtitle] t=6.0s — Subtitle text is very large and takes up a significant portion of the frame, potentially obscuring important visual content.
    ⚠️ [subtitle] t=10.0s — Subtitle text is very large and takes up a significant portion of the frame, potentially obscuring important visual content.
    ⚠️ [subtitle] t=14.0s — Subtitle text is very large and takes up a significant portion of the frame, potentially obscuring important visual content.
    ⚠️ [subtitle] t=18.0s — Subtitle text is very large and takes up a significant portion of the frame, potentially obscuring important visual content.
    ⚠️ [subtitle] t=22.0s — Subtitle text is very large and takes up a significant portion of the frame, potentially obscuring important visual content.
    ⚠️ [subtitle] t=25.3s — Subtitle text is very large and takes up a significant portion of the frame, potentially obscuring important visual content.

**Action taken:**
Issues logged. Review and update guidelines if pattern repeats.

**Expected effect:**
If same issue appears in 3+ videos, add a rule to director_guidelines.json.

**Conflicts with existing rules:**
None.

**Status:** ⏳ Monitor — no action taken yet

---

## [7] 2026-06-06 · ANALYTICS — QA issues in youtube.mp4

**Source:** qa_agent.qa_check() on youtube.mp4

**Analysis:**
Gemini Vision found 4 issue(s):
    ❌ [content] t=14.0s — Narration mentions hidden alleys but footage shows a European-style stone alleyway, not typical of Beijing.
    ❌ [content] t=18.0s — Narration mentions hoisin sauce and garlic, but the footage shows a street food stall with various meats, not specifically roast duck or the condiments mentioned.
    ❌ [content] t=22.0s — Narration mentions foie gras and caviar, but the footage shows a platter of cured meats, cheese, and berries, which does not match the description.
    ❌ [content] t=23.9s — Narration mentions old Beijing neighborhoods, but the footage shows a display that appears to be an exhibition about the Temple of Heaven, not a typical neighborhood scene.

**Action taken:**
Issues logged. Review and update guidelines if pattern repeats.

**Expected effect:**
If same issue appears in 3+ videos, add a rule to director_guidelines.json.

**Conflicts with existing rules:**
None.

**Status:** ⏳ Monitor — no action taken yet

---

## [8] 2026-06-06 · ANALYTICS — QA issues in youtube.mp4

**Source:** qa_agent.qa_check() on youtube.mp4

**Analysis:**
Gemini Vision found 1 issue(s):
    ❌ [content] t=9.7s — Narration says 'This is the sound of perfection' but the frame shows someone clapping their hands with flour, not related to duck preparation.

**Action taken:**
Issues logged. Review and update guidelines if pattern repeats.

**Expected effect:**
If same issue appears in 3+ videos, add a rule to director_guidelines.json.

**Conflicts with existing rules:**
None.

**Status:** ⏳ Monitor — no action taken yet

---

## [9] 2026-06-08 · ANALYTICS — QA issues in youtube.mp4

**Source:** qa_agent.qa_check() on youtube.mp4

**Analysis:**
Gemini Vision found 2 issue(s):
    ❌ [content] t=15.8s — Narration says 'One duck, 108 slices, all carved by hand carefully' but the frame shows a carved stone slab on stairs — no duck or carving in sight. This is a content mismatch.
    ❌ [content] t=11.3s — Narration says 'Chefs pump air under the skin for a crispy secret' but the frame shows a chef working in a kitchen with no visible duck or air-pumping action. The footage does not match the narration.

**Action taken:**
Issues logged. Review and update guidelines if pattern repeats.

**Expected effect:**
If same issue appears in 3+ videos, add a rule to director_guidelines.json.

**Conflicts with existing rules:**
None.

**Status:** ⏳ Monitor — no action taken yet

---

## [10] 2026-06-08 · ANALYTICS — QA issues in youtube.mp4

**Source:** qa_agent.qa_check() on youtube.mp4

**Analysis:**
Gemini Vision found 4 issue(s):
    ❌ [content] t=17.3s — Narration claims 'One duck, 108 slices, all carved by hand in Beijing's tradition' but the frame shows a whole roasted duck on a spit, not sliced or being carved — content mismatch
    ❌ [content] t=18.8s — Narration refers to slicing technique but frame shows uncut roasted duck rotating on spit — no carving visible, content mismatch
    ❌ [content] t=20.3s — Narration mentions hand-carved slices but footage shows intact roasted duck — no evidence of slicing, content mismatch
    ❌ [content] t=21.8s — Narration talks about traditional hand carving but frame shows whole duck on rotisserie — no carving action shown, content mismatch

**Action taken:**
Issues logged. Review and update guidelines if pattern repeats.

**Expected effect:**
If same issue appears in 3+ videos, add a rule to director_guidelines.json.

**Conflicts with existing rules:**
None.

**Status:** ⏳ Monitor — no action taken yet

---

## [11] 2026-06-08 · ANALYTICS — QA issues in youtube.mp4

**Source:** qa_agent.qa_check() on youtube.mp4

**Analysis:**
Gemini Vision found 2 issue(s):
    ❌ [content] t=6.8s — Narration says 'Chefs pump air under the skin for crispness' but the frame shows slicing of cooked duck, not the air-pumping process — content mismatch.
    ❌ [content] t=9.8s — Narration says 'It hangs over fruitwood fire for hours' but the frame shows a close-up of fire and coals without clearly showing a duck hanging over it — visual evidence of roasting is missing or ambiguous.

**Action taken:**
Issues logged. Review and update guidelines if pattern repeats.

**Expected effect:**
If same issue appears in 3+ videos, add a rule to director_guidelines.json.

**Conflicts with existing rules:**
None.

**Status:** ⏳ Monitor — no action taken yet

---

## [12] 2026-06-08 · ANALYTICS — QA issues in youtube.mp4

**Source:** qa_agent.qa_check() on youtube.mp4

**Analysis:**
Gemini Vision found 2 issue(s):
    ❌ [visual] t=17.3s — Black frame with no visible content, likely a transition or technical error, but it's not a proper visual for the narration 'Can you find this place? Drop the name in comments.'
    ? [visual] t=18.8s — Narration refers to a Beijing roast duck location, but the frame shows a street food stall with what appears to be Vietnamese-style meat (possibly pork) and signage in Vietnamese ('PHI YAN'), indicating a mismatch in location and cuisine.

**Action taken:**
Issues logged. Review and update guidelines if pattern repeats.

**Expected effect:**
If same issue appears in 3+ videos, add a rule to director_guidelines.json.

**Conflicts with existing rules:**
None.

**Status:** ⏳ Monitor — no action taken yet

---

## [13] 2026-06-08 · ANALYTICS — QA issues in youtube.mp4

**Source:** qa_agent.qa_check() on youtube.mp4

**Analysis:**
Gemini Vision found 2 issue(s):
    ❌ [content] t=11.3s — Narration says 'Hanging over fruitwood fire for hours slowly' but the frame shows a person handling cooked meat on trays, not ducks hanging over a fire — content mismatch
    ❌ [content] t=12.8s — Narration continues about hanging over fruitwood fire, but frame shows prepared meat on trays — does not match the described process

**Action taken:**
Issues logged. Review and update guidelines if pattern repeats.

**Expected effect:**
If same issue appears in 3+ videos, add a rule to director_guidelines.json.

**Conflicts with existing rules:**
None.

**Status:** ⏳ Monitor — no action taken yet

---

## [14] 2026-06-08 · ANALYTICS — QA issues in youtube.mp4

**Source:** qa_agent.qa_check() on youtube.mp4

**Analysis:**
Gemini Vision found 1 issue(s):
    ❌ [content] t=14.3s — Narration refers to a specific 'restaurant' but the frame shows a generic meat stall with various roasted meats including duck, chicken, and pork — not clearly identifiable as the specific Peking roast duck restaurant mentioned. The visual does not confirm it's the same location referenced in prior frames.

**Action taken:**
Issues logged. Review and update guidelines if pattern repeats.

**Expected effect:**
If same issue appears in 3+ videos, add a rule to director_guidelines.json.

**Conflicts with existing rules:**
None.

**Status:** ⏳ Monitor — no action taken yet

---

## [15] 2026-06-09 · ANALYTICS — QA issues in youtube.mp4

**Source:** qa_agent.qa_check() on youtube.mp4

**Analysis:**
Gemini Vision found 1 issue(s):
    ❌ [content] t=14.3s — Narration says 'Guilin's Li River looks like an ink painting' but the frame shows a man with cormorants on a boat — this is not a clear depiction of the river itself, nor does it visually represent the 'ink painting' aesthetic; the focus is on the person and birds rather than the landscape.

**Action taken:**
Issues logged. Review and update guidelines if pattern repeats.

**Expected effect:**
If same issue appears in 3+ videos, add a rule to director_guidelines.json.

**Conflicts with existing rules:**
None.

**Status:** ⏳ Monitor — no action taken yet

---

## [16] 2026-06-09 · ANALYTICS — QA issues in youtube.mp4

**Source:** qa_agent.qa_check() on youtube.mp4

**Analysis:**
Gemini Vision found 2 issue(s):
    ❌ [content] t=8.3s — Narration says 'The skin shatters like glass — that's the test.' but the frame shows a close-up of duck being sliced, not the skin shattering visibly. The action does not clearly demonstrate the 'shattering' quality described in the narration.
    ❌ [visual] t=14.3s — Frame is significantly darkened or faded, possibly due to a transition effect, making the content hard to see and reducing visual clarity.

**Action taken:**
Issues logged. Review and update guidelines if pattern repeats.

**Expected effect:**
If same issue appears in 3+ videos, add a rule to director_guidelines.json.

**Conflicts with existing rules:**
None.

**Status:** ⏳ Monitor — no action taken yet

---

## [17] 2026-06-10 · ANALYTICS — QA issues in youtube.mp4

**Source:** qa_agent.qa_check() on youtube.mp4

**Analysis:**
Gemini Vision found 1 issue(s):
    ❌ [content] t=8.3s — Narration says 'Chefs separate skin from meat with air' but the frame shows a chef slicing cooked duck meat on a cutting board — this does not visually demonstrate the separation of skin and meat using air, which is a specific technique. The footage shows standard slicing, not the air-inflation method.

**Action taken:**
Issues logged. Review and update guidelines if pattern repeats.

**Expected effect:**
If same issue appears in 3+ videos, add a rule to director_guidelines.json.

**Conflicts with existing rules:**
None.

**Status:** ⏳ Monitor — no action taken yet

---

## [18] 2026-06-10 · ANALYTICS — QA issues in youtube.mp4

**Source:** qa_agent.qa_check() on youtube.mp4

**Analysis:**
Gemini Vision found 3 issue(s):
    ❌ [content] t=14.3s — Narration says 'Eat the crackling skin first, dipped in sugar.' but the frame shows a street food stall with hanging meat that appears to be pork belly or char siu, not Peking duck skin. The visual does not clearly show Peking duck skin being eaten or dipped in sugar.
    ❌ [content] t=15.8s — Narration refers to Peking duck skin, but the footage shows what appears to be pork belly or another type of roasted meat at a street stall. No clear evidence of Peking duck skin is visible.
    ❌ [content] t=17.3s — Narration continues about eating Peking duck skin, but the frame still shows non-specific roasted meat at a market stall — not identifiable as Peking duck skin. Content mismatch.

**Action taken:**
Issues logged. Review and update guidelines if pattern repeats.

**Expected effect:**
If same issue appears in 3+ videos, add a rule to director_guidelines.json.

**Conflicts with existing rules:**
None.

**Status:** ⏳ Monitor — no action taken yet

---

## [19] 2026-06-10 · ANALYTICS — QA issues in youtube.mp4

**Source:** qa_agent.qa_check() on youtube.mp4

**Analysis:**
Gemini Vision found 1 issue(s):
    ❌ [content] t=8.3s — Narration says 'Chefs separate skin from meat with air' — this refers to the traditional technique of inflating the duck before roasting to separate skin and meat. However, the frame shows slicing already cooked duck, not the separation process. This is a content mismatch as it does not visually represent the described technique.

**Action taken:**
Issues logged. Review and update guidelines if pattern repeats.

**Expected effect:**
If same issue appears in 3+ videos, add a rule to director_guidelines.json.

**Conflicts with existing rules:**
None.

**Status:** ⏳ Monitor — no action taken yet

---

## [20] 2026-06-10 · ANALYTICS — QA issues in youtube.mp4

**Source:** qa_agent.qa_check() on youtube.mp4

**Analysis:**
Gemini Vision found 1 issue(s):
    ❌ [content] t=14.3s — Narration says 'You eat the crackling skin first, dipped in sugar.' but frame shows a black screen — no visual content to support the claim. This is a content mismatch as it fails to show the action described.

**Action taken:**
Issues logged. Review and update guidelines if pattern repeats.

**Expected effect:**
If same issue appears in 3+ videos, add a rule to director_guidelines.json.

**Conflicts with existing rules:**
None.

**Status:** ⏳ Monitor — no action taken yet

---

## [21] 2026-06-10 · ANALYTICS — QA issues in youtube.mp4

**Source:** qa_agent.qa_check() on youtube.mp4

**Analysis:**
Gemini Vision found 1 issue(s):
    ❌ [content] t=8.3s — Narration says 'Chefs separate skin from meat with air' — this refers to inflating the duck before roasting, but the frame shows slicing already cooked duck meat, not the separation process. The visual does not match the culinary technique being described.

**Action taken:**
Issues logged. Review and update guidelines if pattern repeats.

**Expected effect:**
If same issue appears in 3+ videos, add a rule to director_guidelines.json.

**Conflicts with existing rules:**
None.

**Status:** ⏳ Monitor — no action taken yet

---

## [22] 2026-06-10 · ANALYTICS — QA issues in youtube.mp4

**Source:** qa_agent.qa_check() on youtube.mp4

**Analysis:**
Gemini Vision found 2 issue(s):
    ❌ [content] t=8.3s — Narration says 'Chefs separate the skin from the meat with air' — this refers to inflating the duck before roasting, but the frame shows sliced duck being carved, not the separation process. The visual does not match the culinary technique described.
    ❌ [content] t=14.3s — Narration says 'You eat the crackling skin first, dipped in sugar' — this is specific to Peking duck tradition, but the frame shows two whole chickens roasting on a spit over an open fire, which is not Peking duck and not consistent with the context of Beijing roast duck preparation or serving.

**Action taken:**
Issues logged. Review and update guidelines if pattern repeats.

**Expected effect:**
If same issue appears in 3+ videos, add a rule to director_guidelines.json.

**Conflicts with existing rules:**
None.

**Status:** ⏳ Monitor — no action taken yet

---

## [23] 2026-06-10 · ANALYTICS — QA issues in youtube.mp4

**Source:** qa_agent.qa_check() on youtube.mp4

**Analysis:**
Gemini Vision found 1 issue(s):
    ❌ [content] t=35.3s — Narration refers to Peking duck being carved tableside and eaten with white sugar, but the frame shows two whole chickens roasting on a spit over an open fire — not Peking duck preparation or serving. This is a clear content mismatch.

**Action taken:**
Issues logged. Review and update guidelines if pattern repeats.

**Expected effect:**
If same issue appears in 3+ videos, add a rule to director_guidelines.json.

**Conflicts with existing rules:**
None.

**Status:** ⏳ Monitor — no action taken yet

---
