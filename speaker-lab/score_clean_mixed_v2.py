from __future__ import annotations

import json
import statistics
import time
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
MIXED_DIR = Path("/mixed")
MODEL_DIR = Path("/models/spkrec-ecapa-voxceleb")
OUTPUT = Path("/output/clean-mixed-v2-scores.json")

TARGET_RATE = 16000


AARON_FILES = {
    "1786395986654175298-item_EBRUN9iTONzthjtAjwsIj.wav",
    "1786396019041709395-item_EBRUvyqVVfPxz9QH3L5Eg.wav",
    "1786396062650917304-item_EBRVbI9r5YpbTI3XP6Jl3.wav",
}

BACKGROUND_FILES = {
    "1786395988047542303-item_EBRUQJrTSAFwhit0hn1b8.wav",
    "1786396024891276717-item_EBRV1KoHxKMWnArcqWYBQ.wav",
    "1786396033545974775-item_EBRVAr4UrQ1gZRzH5V5zf.wav",
    "1786396034922810459-item_EBRVBgMFsEeN1SKHLaouE.wav",
}


def load_audio(path: Path) -> torch.Tensor:
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


def embed(
    model: EncoderClassifier,
    path: Path,
) -> torch.Tensor:
    waveform = load_audio(path)

    with torch.inference_mode():
        value = model.encode_batch(
            waveform
        )

    value = (
        value
        .squeeze()
        .detach()
        .cpu()
        .float()
    )

    return F.normalize(
        value,
        dim=0,
    )


def cosine(a, b) -> float:
    return float(
        torch.dot(
            F.normalize(a, dim=0),
            F.normalize(b, dim=0),
        ).item()
    )


def build_refs(
    model,
    root,
):
    return {
        path.name: embed(
            model,
            path,
        )
        for path in sorted(
            root.glob("*.wav")
        )
    }


def centroid(refs):
    return F.normalize(
        torch.stack(
            list(refs.values())
        ).mean(dim=0),
        dim=0,
    )


def top3(
    candidate,
    refs,
):
    scores = sorted(
        (
            cosine(
                candidate,
                ref,
            )
            for ref in refs.values()
        ),
        reverse=True,
    )

    return statistics.fmean(
        scores[:3]
    )


torch.set_num_threads(4)

print(
    "========== LOAD ECAPA =========="
)

started = time.perf_counter()

model = EncoderClassifier.from_hparams(
    source="speechbrain/spkrec-ecapa-voxceleb",
    savedir=str(MODEL_DIR),
    run_opts={
        "device": "cpu",
    },
)

print(
    "PASS: model loaded in",
    round(
        (
            time.perf_counter()
            - started
        ) * 1000,
        1,
    ),
    "ms",
)


long_refs = build_refs(
    model,
    LONG_DIR,
)

short_refs = build_refs(
    model,
    SHORT_DIR,
)

tv_refs = build_refs(
    model,
    TV_DIR,
)

print()
print(
    "Long Aaron refs:",
    len(long_refs),
)

print(
    "Short Aaron refs:",
    len(short_refs),
)

print(
    "TV refs:",
    len(tv_refs),
)


if len(long_refs) != 7:
    raise SystemExit(
        "FAIL: long Aaron profile"
    )

if len(short_refs) != 10:
    raise SystemExit(
        "FAIL: short Aaron profile"
    )

if len(tv_refs) != 9:
    raise SystemExit(
        "FAIL: TV profile"
    )


combined_refs = {
    **{
        f"L/{k}": v
        for k, v in long_refs.items()
    },
    **{
        f"S/{k}": v
        for k, v in short_refs.items()
    },
}


long_c = centroid(
    long_refs
)

short_c = centroid(
    short_refs
)

combined_c = centroid(
    combined_refs
)

tv_c = centroid(
    tv_refs
)


mixed_files = sorted(
    MIXED_DIR.glob("*.wav")
)

actual_names = {
    path.name
    for path in mixed_files
}

expected_names = (
    AARON_FILES
    | BACKGROUND_FILES
)

if actual_names != expected_names:
    print(
        "Expected:",
        sorted(expected_names),
    )

    print(
        "Found:",
        sorted(actual_names),
    )

    raise SystemExit(
        "FAIL: clean mixed dataset "
        "does not match expected 7 WAVs"
    )


records = []

print()
print(
    "========== CLEAN MIXED V2 SCORES =========="
)


for path in mixed_files:
    candidate = embed(
        model,
        path,
    )

    metadata_path = (
        MIXED_DIR
        / f"{path.stem}.json"
    )

    metadata = json.loads(
        metadata_path.read_text()
    )

    transcript = str(
        metadata.get(
            "transcript",
            "",
        )
    )

    label = (
        "AARON"
        if path.name in AARON_FILES
        else "BACKGROUND"
    )

    duration = float(
        sf.info(path).duration
    )

    long_score = cosine(
        candidate,
        long_c,
    )

    short_score = cosine(
        candidate,
        short_c,
    )

    combined_score = cosine(
        candidate,
        combined_c,
    )

    tv_score = cosine(
        candidate,
        tv_c,
    )

    long_margin = (
        long_score - tv_score
    )

    short_margin = (
        short_score - tv_score
    )

    combined_margin = (
        combined_score - tv_score
    )

    long_top3 = top3(
        candidate,
        long_refs,
    )

    short_top3 = top3(
        candidate,
        short_refs,
    )

    tv_top3 = top3(
        candidate,
        tv_refs,
    )

    best_profile_margin = (
        max(
            long_score,
            short_score,
        )
        - tv_score
    )

    best_top3_margin = (
        max(
            long_top3,
            short_top3,
        )
        - tv_top3
    )

    record = {
        "wav": path.name,
        "label": label,
        "transcript": transcript,
        "duration_seconds": round(
            duration,
            3,
        ),
        "long": round(
            long_score,
            6,
        ),
        "short": round(
            short_score,
            6,
        ),
        "combined": round(
            combined_score,
            6,
        ),
        "tv": round(
            tv_score,
            6,
        ),
        "long_margin": round(
            long_margin,
            6,
        ),
        "short_margin": round(
            short_margin,
            6,
        ),
        "combined_margin": round(
            combined_margin,
            6,
        ),
        "best_profile_margin": round(
            best_profile_margin,
            6,
        ),
        "best_top3_margin": round(
            best_top3_margin,
            6,
        ),
    }

    records.append(
        record
    )


for record in records:
    print()

    print(
        record["label"],
        "|",
        f"{record['duration_seconds']:.3f}s",
        "|",
        repr(
            record["transcript"]
        ),
    )

    print(
        "  long:",
        f"{record['long']:.6f}",
    )

    print(
        "  short:",
        f"{record['short']:.6f}",
    )

    print(
        "  combined:",
        f"{record['combined']:.6f}",
    )

    print(
        "  TV:",
        f"{record['tv']:.6f}",
    )

    print(
        "  short margin:",
        f"{record['short_margin']:.6f}",
    )

    print(
        "  best profile margin:",
        f"{record['best_profile_margin']:.6f}",
    )

    print(
        "  best top3 margin:",
        f"{record['best_top3_margin']:.6f}",
    )


print()
print(
    "========== CLEAN SEPARATION =========="
)


features = (
    "long",
    "short",
    "combined",
    "long_margin",
    "short_margin",
    "combined_margin",
    "best_profile_margin",
    "best_top3_margin",
)

separation = {}


for feature in features:
    positives = [
        item[feature]
        for item in records
        if item["label"] == "AARON"
    ]

    negatives = [
        item[feature]
        for item in records
        if item["label"] == "BACKGROUND"
    ]

    positive_min = min(
        positives
    )

    negative_max = max(
        negatives
    )

    gap = (
        positive_min
        - negative_max
    )

    midpoint = (
        positive_min
        + negative_max
    ) / 2.0

    separation[feature] = {
        "aaron_min": positive_min,
        "background_max":
            negative_max,
        "gap": round(
            gap,
            6,
        ),
        "midpoint": round(
            midpoint,
            6,
        ),
    }

    print()
    print(feature)

    print(
        "  Aaron minimum:      ",
        f"{positive_min:.6f}",
    )

    print(
        "  Background maximum: ",
        f"{negative_max:.6f}",
    )

    print(
        "  Gap:                ",
        f"{gap:+.6f}",
    )

    print(
        "  Midpoint:           ",
        f"{midpoint:.6f}",
    )

    print(
        "  Verdict:",
        "SEPARATED"
        if gap > 0
        else "OVERLAP",
    )


OUTPUT.parent.mkdir(
    parents=True,
    exist_ok=True,
)

OUTPUT.write_text(
    json.dumps(
        {
            "gate_enabled": False,
            "dataset": "clean-mixed-v2",
            "aaron_count": 3,
            "background_count": 4,
            "records": records,
            "separation": separation,
        },
        indent=2,
    )
    + "\n"
)


print()
print(
    "========== RESULT =========="
)

winners = [
    feature
    for feature, values
    in separation.items()
    if values["gap"] > 0
]

if winners:
    print(
        "SEPARATING FEATURES:",
        ", ".join(
            winners
        ),
    )
else:
    print(
        "NO FEATURE FULLY SEPARATES "
        "THE CLEAN RETRY"
    )

print()
print(
    "SPEAKER GATE REMAINS DISABLED"
)
