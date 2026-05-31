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
