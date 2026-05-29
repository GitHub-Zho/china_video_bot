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
