"""
Music Agent — deterministic BGM selection with beat alignment (选曲 + 卡点).

Design principle: NO AI judgement anywhere in this module. Everything is
measurable signal processing (librosa) + explicit rules the user can read
and edit. No emotion models, no hallucination surface.

Two stages:

  1. INDEX (offline, once per track)
     Each file in assets/music/ gets a JSON sidecar with:
       tempo (BPM), beat grid (timestamps), RMS energy curve,
       spectral brightness, and a rule-based bucket (upbeat/chill/cinematic).

  2. SELECT (per video)
     Scene cut times are FIXED by the voice track — we never move them.
     Instead we search (track × start_offset) for the combination whose
     beat grid best coincides with the existing cuts:

         for each track, for offset in 0..8s step 50ms:
             score = how many scene cuts land within ±80ms of a beat

     Pure argmax. Same inputs → same choice. A debug report shows exactly
     which cuts hit which beats.

Bucket routing: config/music_map.json maps topic keywords → bucket
(user-editable). Unknown topics use the "default" bucket.
"""
import json
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
MUSIC_DIR  = _PROJECT_ROOT / "assets" / "music"
MAP_FILE   = _PROJECT_ROOT / "config" / "music_map.json"
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg"}
INDEX_VERSION = 1

# Beat-alignment scoring
TIGHT_MS = 0.080   # cut within ±80ms of a beat = full hit
LOOSE_MS = 0.150   # within ±150ms = half credit
OFFSET_MAX  = 8.0  # search music start offsets 0..8s
OFFSET_STEP = 0.05


# ── Stage 1: indexing ─────────────────────────────────────────────────────────

def _bucket_for(tempo: float, rms_mean: float, centroid: float) -> str:
    """Rule-based mood bucket from measurable features — thresholds, not models.
    upbeat:    fast and loud        (street food, markets, lists)
    chill:     slow and dark-toned  (scenery, daily life)
    cinematic: everything between   (culture, history, process)
    """
    if tempo >= 112 and rms_mean >= 0.10:
        return "upbeat"
    if tempo <= 96 and centroid <= 2400:
        return "chill"
    return "cinematic"


def _analyse_track(path: Path) -> dict | None:
    """librosa feature pass for one file. Returns index dict or None on failure."""
    try:
        import librosa
        import numpy as np
    except ImportError:
        print("  [Music] librosa not installed — run: pip install librosa")
        return None
    try:
        y, sr = librosa.load(str(path), sr=22050, mono=True)
        duration = len(y) / sr
        tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="time")
        tempo = float(np.atleast_1d(tempo)[0])
        rms = librosa.feature.rms(y=y, hop_length=1024)[0]
        # Downsample energy curve to 2 Hz for the sidecar (enough to see structure)
        hop_sec = 1024 / sr
        step = max(1, int(0.5 / hop_sec))
        energy = [round(float(v), 4) for v in rms[::step]]
        centroid = float(librosa.feature.spectral_centroid(y=y, sr=sr).mean())
        return {
            "version":  INDEX_VERSION,
            "file":     path.name,
            "duration": round(duration, 2),
            "tempo":    round(tempo, 1),
            "beats":    [round(float(b), 3) for b in beats],
            "energy_2hz": energy,
            "centroid": round(centroid, 0),
            "rms_mean": round(float(rms.mean()), 4),
            "bucket":   _bucket_for(tempo, float(rms.mean()), centroid),
        }
    except Exception as e:
        print(f"  [Music] analyse failed for {path.name}: {e}")
        return None


def index_library(music_dir: Path = MUSIC_DIR) -> list[dict]:
    """Index every track (skip up-to-date sidecars). Returns list of index dicts."""
    if not music_dir.exists():
        return []
    indexed = []
    for f in sorted(music_dir.iterdir()):
        if f.suffix.lower() not in AUDIO_EXTS:
            continue
        sidecar = f.with_suffix(f.suffix + ".json")
        if sidecar.exists():
            try:
                data = json.loads(sidecar.read_text())
                if (data.get("version") == INDEX_VERSION
                        and sidecar.stat().st_mtime >= f.stat().st_mtime):
                    data["path"] = str(f)
                    indexed.append(data)
                    continue
            except Exception:
                pass
        print(f"  [Music] Indexing {f.name}…")
        data = _analyse_track(f)
        if data:
            sidecar.write_text(json.dumps(data, indent=1))
            data["path"] = str(f)
            indexed.append(data)
            print(f"  [Music]   {data['tempo']:.0f} BPM · {data['bucket']} · "
                  f"{data['duration']:.0f}s · {len(data['beats'])} beats")
    return indexed


# ── Stage 2: selection ────────────────────────────────────────────────────────

def _topic_bucket(topic: str) -> str:
    """Keyword → bucket from the user-editable map. Deterministic."""
    try:
        cfg = json.loads(MAP_FILE.read_text())
    except Exception:
        cfg = {"map": {}, "default": "cinematic"}
    t = (topic or "").lower()
    for kw, bucket in cfg.get("map", {}).items():
        if kw.lower() in t:
            return bucket
    return cfg.get("default", "cinematic")


def _alignment_score(cuts, beats, offset: float) -> tuple[float, float]:
    """(score, mean_distance) of scene cuts against a beat grid at `offset`.
    Music time for video-time t is (t + offset)."""
    import numpy as np
    beats = np.asarray(beats)
    if len(beats) == 0:
        return 0.0, 9.9
    times = np.asarray(cuts) + offset
    idx = np.searchsorted(beats, times)
    idx_lo = np.clip(idx - 1, 0, len(beats) - 1)
    idx_hi = np.clip(idx,     0, len(beats) - 1)
    dist = np.minimum(np.abs(times - beats[idx_lo]),
                      np.abs(times - beats[idx_hi]))
    score = float((dist <= TIGHT_MS).sum()) + 0.5 * float(
        ((dist > TIGHT_MS) & (dist <= LOOSE_MS)).sum())
    return score, float(dist.mean())


def pick_music_for_video(video_id: str, scene_durations: list[float],
                         topic: str = "",
                         music_dir: Path = MUSIC_DIR) -> tuple[str, float] | None:
    """
    Choose (track_path, start_offset) for this video.

    scene_durations: FIXED cut layout from the voice track — never modified.
    Returns None when the library is empty (pipeline then runs without BGM).
    """
    import numpy as np
    tracks = index_library(music_dir)
    if not tracks or not scene_durations:
        return None

    total = float(sum(scene_durations))
    # Internal cut points only (t=0 and the final end don't need beat hits)
    cuts = list(np.cumsum(scene_durations)[:-1])
    if not cuts:
        return None

    bucket = _topic_bucket(topic)
    pool = [t for t in tracks if t["bucket"] == bucket] or tracks

    best = None   # (score, -mean_dist, name, offset, track)
    for tr in pool:
        max_off = max(0.0, min(OFFSET_MAX, tr["duration"] - total))
        offsets = np.arange(0.0, max_off + 1e-9, OFFSET_STEP) if max_off > 0 else [0.0]
        for off in offsets:
            score, mdist = _alignment_score(cuts, tr["beats"], float(off))
            key = (score, -mdist, tr["file"], float(off), tr)
            if best is None or key[:2] > best[:2]:
                best = key

    if best is None:
        return None
    score, neg_mdist, name, offset, tr = best

    # Debug report — every decision inspectable
    print(f"  [Music] 🎵 {name} @ offset {offset:.2f}s "
          f"(bucket={bucket}, {tr['tempo']:.0f} BPM) — "
          f"score {score:.1f}/{len(cuts)} cuts on beat")
    beats = np.asarray(tr["beats"])
    for c in cuts:
        t = c + offset
        d = float(np.abs(beats - t).min()) if len(beats) else 9.9
        mark = "✓" if d <= TIGHT_MS else ("~" if d <= LOOSE_MS else "✗")
        print(f"  [Music]    cut@{c:5.2f}s → nearest beat Δ{d*1000:4.0f}ms {mark}")
    return tr["path"], float(offset)
