from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
import torch.nn.functional as F
import torchaudio

from speechbrain.inference.speaker import EncoderClassifier


LONG_DIR = Path("/long")
SHORT_DIR = Path("/short")
TV_DIR = Path("/tv")
CLEAN_DIR = Path("/clean")
HOLDOUT_DIR = Path("/holdout")
LIVE_V1_DIR = Path("/live-v1")
LIVE_V2_DIR = Path("/live-v2")
MODEL_DIR = Path(
    "/models/spkrec-ecapa-voxceleb"
)
OUTPUT = Path(
    "/output/window-evidence-v1.json"
)

TARGET_RATE = 16000
WINDOW_SECONDS = 1.00
STEP_SECONDS = 0.25


CLEAN_AARON = {
    "1786395986654175298-item_EBRUN9iTONzthjtAjwsIj.wav",
    "1786396019041709395-item_EBRUvyqVVfPxz9QH3L5Eg.wav",
    "1786396062650917304-item_EBRVbI9r5YpbTI3XP6Jl3.wav",
}

CLEAN_BACKGROUND = {
    "1786395988047542303-item_EBRUQJrTSAFwhit0hn1b8.wav",
    "1786396024891276717-item_EBRV1KoHxKMWnArcqWYBQ.wav",
    "1786396033545974775-item_EBRVAr4UrQ1gZRzH5V5zf.wav",
    "1786396034922810459-item_EBRVBgMFsEeN1SKHLaouE.wav",
}

HOLDOUT_AARON = {
    "1786468490415274437-item_EBkLoUcTOJHOvdgV0oNqe.wav",
    "1786468502317363366-item_EBkLzrOJiuZkGHeT2f8Dw.wav",
    "1786468514148283607-item_EBkMBdWqISs2kvczN3osW.wav",
    "1786468526503624408-item_EBkMNu2KvKn6a7fiVpHbu.wav",
    "1786468553970486393-item_EBkMorPYI2gY95RUyJXcl.wav",
    "1786468564118876885-item_EBkMzI02dMoq5csd3UGvw.wav",
}

HOLDOUT_BACKGROUND = {
    "1786468482053752467-item_EBkLhiYWfwVNpMa977xls.wav",
    "1786468515610664281-item_EBkMEgFu23FTztvoYu9f7.wav",
    "1786468538487361911-item_EBkMZ5fZLKCMIeshZjOwn.wav",
}

HOLDOUT_WAKE = {
    "1786468497239590084-item_EBkLvAtm2CTraysPZyymU.wav",
    "1786468508673188271-item_EBkM7VVcVFvQ2RGXVQOoa.wav",
    "1786468521069797793-item_EBkMJrofPDKsF5avhcamX.wav",
    "1786468547515842425-item_EBkMkQ7ogGcamVXdrmUQ4.wav",
    "1786468558977848151-item_EBkMv6voLkzNxWpZhSQu1.wav",
}


def load_audio(path):
    audio, rate = sf.read(
        path,
        dtype="float32",
        always_2d=False,
    )

    audio = np.asarray(audio)

    if audio.ndim == 2:
        audio = audio.mean(axis=1)

    waveform = (
        torch.from_numpy(audio)
        .float()
        .unsqueeze(0)
    )

    if rate != TARGET_RATE:
        waveform = torchaudio.functional.resample(
            waveform,
            rate,
            TARGET_RATE,
        )

    return waveform


def embedding(model, waveform):
    with torch.inference_mode():
        value = model.encode_batch(
            waveform
        )

    return F.normalize(
        value
        .squeeze()
        .detach()
        .cpu()
        .float(),
        dim=0,
    )


def embed_file(model, path):
    return embedding(
        model,
        load_audio(path),
    )


def cosine(left, right):
    return float(
        torch.dot(
            F.normalize(left, dim=0),
            F.normalize(right, dim=0),
        ).item()
    )


def refs(model, root):
    return {
        p.name: embed_file(model, p)
        for p in sorted(
            root.glob("*.wav")
        )
    }


def centroid(values):
    return F.normalize(
        torch.stack(
            list(values.values())
        ).mean(dim=0),
        dim=0,
    )


def transcript(path):
    metadata = path.with_suffix(
        ".json"
    )

    if not metadata.exists():
        return ""

    try:
        return str(
            json.loads(
                metadata.read_text()
            ).get(
                "transcript",
                "",
            )
        )
    except Exception:
        return ""


torch.set_num_threads(4)

print(
    "========== LOAD ECAPA =========="
)

model = EncoderClassifier.from_hparams(
    source=(
        "speechbrain/"
        "spkrec-ecapa-voxceleb"
    ),
    savedir=str(MODEL_DIR),
    run_opts={
        "device": "cpu",
    },
)

long_refs = refs(
    model,
    LONG_DIR,
)

short_refs = refs(
    model,
    SHORT_DIR,
)

tv_refs = refs(
    model,
    TV_DIR,
)

if len(long_refs) != 7:
    raise SystemExit(
        "FAIL: expected 7 long refs"
    )

if len(short_refs) != 10:
    raise SystemExit(
        "FAIL: expected 10 short refs"
    )

if len(tv_refs) != 9:
    raise SystemExit(
        "FAIL: expected 9 TV refs"
    )

long_c = centroid(
    long_refs
)

short_c = centroid(
    short_refs
)

tv_c = centroid(
    tv_refs
)

records = []


def analyse(
    dataset,
    label,
    path,
):
    waveform = load_audio(
        path
    )

    full_emb = embedding(
        model,
        waveform,
    )

    full_long = cosine(
        full_emb,
        long_c,
    )

    full_short = cosine(
        full_emb,
        short_c,
    )

    full_tv = cosine(
        full_emb,
        tv_c,
    )

    full_best = max(
        full_long,
        full_short,
    )

    duration = (
        waveform.shape[-1]
        / TARGET_RATE
    )

    window_samples = round(
        WINDOW_SECONDS
        * TARGET_RATE
    )

    step_samples = round(
        STEP_SECONDS
        * TARGET_RATE
    )

    windows = []

    if waveform.shape[-1] >= window_samples:
        starts = range(
            0,
            waveform.shape[-1]
            - window_samples
            + 1,
            step_samples,
        )
    else:
        starts = []

    for start in starts:
        end = (
            start
            + window_samples
        )

        chunk = waveform[
            :,
            start:end,
        ]

        emb = embedding(
            model,
            chunk,
        )

        long_score = cosine(
            emb,
            long_c,
        )

        short_score = cosine(
            emb,
            short_c,
        )

        tv_score = cosine(
            emb,
            tv_c,
        )

        best_score = max(
            long_score,
            short_score,
        )

        windows.append(
            {
                "start":
                    round(
                        start
                        / TARGET_RATE,
                        3,
                    ),
                "end":
                    round(
                        end
                        / TARGET_RATE,
                        3,
                    ),
                "long":
                    long_score,
                "short":
                    short_score,
                "tv":
                    tv_score,
                "best":
                    best_score,
                "short_margin":
                    short_score
                    - tv_score,
                "best_margin":
                    best_score
                    - tv_score,
            }
        )

    fallback = False

    if not windows:
        fallback = True

        windows = [
            {
                "start": 0.0,
                "end":
                    round(
                        duration,
                        3,
                    ),
                "long":
                    full_long,
                "short":
                    full_short,
                "tv":
                    full_tv,
                "best":
                    full_best,
                "short_margin":
                    full_short
                    - full_tv,
                "best_margin":
                    full_best
                    - full_tv,
            }
        ]

    best_short_window = max(
        w["short"]
        for w in windows
    )

    best_short_margin = max(
        w["short_margin"]
        for w in windows
    )

    best_profile_window = max(
        w["best"]
        for w in windows
    )

    best_profile_margin = max(
        w["best_margin"]
        for w in windows
    )

    records.append(
        {
            "dataset":
                dataset,
            "label":
                label,
            "wav":
                path.name,
            "transcript":
                transcript(path),
            "duration":
                round(
                    duration,
                    3,
                ),
            "window_fallback":
                fallback,
            "full_long":
                round(
                    full_long,
                    6,
                ),
            "full_short":
                round(
                    full_short,
                    6,
                ),
            "full_best":
                round(
                    full_best,
                    6,
                ),
            "full_tv":
                round(
                    full_tv,
                    6,
                ),
            "window_short":
                round(
                    best_short_window,
                    6,
                ),
            "window_short_margin":
                round(
                    best_short_margin,
                    6,
                ),
            "window_best":
                round(
                    best_profile_window,
                    6,
                ),
            "window_best_margin":
                round(
                    best_profile_margin,
                    6,
                ),
        }
    )


for path in sorted(
    CLEAN_DIR.glob("*.wav")
):
    if path.name in CLEAN_AARON:
        analyse(
            "clean-v2",
            "AARON",
            path,
        )

    elif path.name in CLEAN_BACKGROUND:
        analyse(
            "clean-v2",
            "BACKGROUND",
            path,
        )


for path in sorted(
    HOLDOUT_DIR.glob("*.wav")
):
    if path.name in HOLDOUT_AARON:
        label = "AARON"

    elif path.name in HOLDOUT_BACKGROUND:
        label = "BACKGROUND"

    elif path.name in HOLDOUT_WAKE:
        label = "WAKE"

    else:
        continue

    analyse(
        "holdout-v1",
        label,
        path,
    )


for path in sorted(
    TV_DIR.glob("*.wav")
):
    analyse(
        "tv-negative-v1",
        "BACKGROUND",
        path,
    )


for path in sorted(
    LIVE_V1_DIR.glob("*.wav")
):
    analyse(
        "live-v1",
        "AARON",
        path,
    )


for path in sorted(
    LIVE_V2_DIR.glob("*.wav")
):
    analyse(
        "live-v2",
        "AARON",
        path,
    )


aaron = [
    r
    for r in records
    if r["label"] == "AARON"
]

negative = [
    r
    for r in records
    if r["label"]
    in (
        "BACKGROUND",
        "WAKE",
    )
]


metrics = [
    "full_best",
    "window_short",
    "window_short_margin",
    "window_best",
    "window_best_margin",
]


print()
print(
    "========== WINDOW EVIDENCE AUDIT =========="
)

print(
    "Aaron samples:",
    len(aaron),
)

print(
    "Negative/wake samples:",
    len(negative),
)


for metric in metrics:
    aaron_min = min(
        r[metric]
        for r in aaron
    )

    negative_max = max(
        r[metric]
        for r in negative
    )

    gap = (
        aaron_min
        - negative_max
    )

    print()
    print(
        metric
    )

    print(
        "  Aaron min:",
        f"{aaron_min:.6f}",
    )

    print(
        "  Negative max:",
        f"{negative_max:.6f}",
    )

    print(
        "  Gap:",
        f"{gap:+.6f}",
    )

    print(
        "  Result:",
        (
            "SEPARATED"
            if gap > 0
            else "OVERLAP"
        ),
    )


print()
print(
    "========== LIVE POSITIVES =========="
)

for r in records:
    if r["dataset"] not in (
        "live-v1",
        "live-v2",
    ):
        continue

    print()
    print(
        r["dataset"],
        "|",
        repr(
            r["transcript"]
        ),
    )

    print(
        "  full_best:",
        f"{r['full_best']:.6f}",
    )

    print(
        "  window_short:",
        f"{r['window_short']:.6f}",
    )

    print(
        "  window_short_margin:",
        f"{r['window_short_margin']:+.6f}",
    )

    print(
        "  window_best:",
        f"{r['window_best']:.6f}",
    )

    print(
        "  window_best_margin:",
        f"{r['window_best_margin']:+.6f}",
    )


print()
print(
    "========== TOP 8 NEGATIVES BY WINDOW SHORT MARGIN =========="
)

for r in sorted(
    negative,
    key=lambda x:
        x["window_short_margin"],
    reverse=True,
)[:8]:

    print(
        f"{r['window_short_margin']:+.6f}",
        "|",
        f"short={r['window_short']:.6f}",
        "|",
        r["dataset"],
        "|",
        repr(
            r["transcript"]
        ),
    )


print()
print(
    "========== LOWEST 8 AARON BY WINDOW SHORT MARGIN =========="
)

for r in sorted(
    aaron,
    key=lambda x:
        x["window_short_margin"],
)[:8]:

    print(
        f"{r['window_short_margin']:+.6f}",
        "|",
        f"short={r['window_short']:.6f}",
        "|",
        r["dataset"],
        "|",
        repr(
            r["transcript"]
        ),
    )


OUTPUT.parent.mkdir(
    parents=True,
    exist_ok=True,
)

OUTPUT.write_text(
    json.dumps(
        {
            "gate_enabled":
                False,
            "window_seconds":
                WINDOW_SECONDS,
            "step_seconds":
                STEP_SECONDS,
            "records":
                records,
        },
        indent=2,
    )
    + "\n"
)

print()
print(
    "========== RESULT =========="
)

print(
    "DIAGNOSTIC ONLY"
)

print(
    "NO PRODUCTION THRESHOLDS CHANGED"
)

print(
    "SPEAKER GATE REMAINS DISABLED"
)
