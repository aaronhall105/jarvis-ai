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
HOLDOUT_DIR = Path("/holdout")
MODEL_DIR = Path("/models/spkrec-ecapa-voxceleb")
OUTPUT = Path("/output/holdout-v1-scores.json")

TARGET_RATE = 16000

# From clean mixed V2 only.
# Diagnostic only. NOT an enabled production threshold.
PRIOR_SHORT_MARGIN_MIDPOINT = 0.175808


AARON_FILES = {
    "1786468490415274437-item_EBkLoUcTOJHOvdgV0oNqe.wav",
    "1786468502317363366-item_EBkLzrOJiuZkGHeT2f8Dw.wav",
    "1786468514148283607-item_EBkMBdWqISs2kvczN3osW.wav",
    "1786468526503624408-item_EBkMNu2KvKn6a7fiVpHbu.wav",
    "1786468553970486393-item_EBkMorPYI2gY95RUyJXcl.wav",
    "1786468564118876885-item_EBkMzI02dMoq5csd3UGvw.wav",
}

BACKGROUND_FILES = {
    "1786468482053752467-item_EBkLhiYWfwVNpMa977xls.wav",
    "1786468515610664281-item_EBkMEgFu23FTztvoYu9f7.wav",
    "1786468538487361911-item_EBkMZ5fZLKCMIeshZjOwn.wav",
}

EXCLUDE_FILES = {
    "1786468497239590084-item_EBkLvAtm2CTraysPZyymU.wav",
    "1786468508673188271-item_EBkM7VVcVFvQ2RGXVQOoa.wav",
    "1786468521069797793-item_EBkMJrofPDKsF5avhcamX.wav",
    "1786468547515842425-item_EBkMkQ7ogGcamVXdrmUQ4.wav",
    "1786468558977848151-item_EBkMv6voLkzNxWpZhSQu1.wav",
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

    if audio.ndim != 1:
        raise RuntimeError(
            f"{path.name}: bad shape {audio.shape}"
        )

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


def cosine(
    left: torch.Tensor,
    right: torch.Tensor,
) -> float:
    return float(
        torch.dot(
            F.normalize(left, dim=0),
            F.normalize(right, dim=0),
        ).item()
    )


def build_refs(
    model: EncoderClassifier,
    root: Path,
) -> dict[str, torch.Tensor]:
    return {
        path.name: embed(
            model,
            path,
        )
        for path in sorted(
            root.glob("*.wav")
        )
    }


def centroid(
    refs: dict[str, torch.Tensor],
) -> torch.Tensor:
    return F.normalize(
        torch.stack(
            list(refs.values())
        ).mean(dim=0),
        dim=0,
    )


def top3(
    candidate: torch.Tensor,
    refs: dict[str, torch.Tensor],
) -> float:
    scores = sorted(
        (
            cosine(candidate, ref)
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
        "FAIL: expected 7 long Aaron refs"
    )

if len(short_refs) != 10:
    raise SystemExit(
        "FAIL: expected 10 short Aaron refs"
    )

if len(tv_refs) != 9:
    raise SystemExit(
        "FAIL: expected 9 TV refs"
    )


combined_refs = {
    **{
        f"L/{name}": value
        for name, value in long_refs.items()
    },
    **{
        f"S/{name}": value
        for name, value in short_refs.items()
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


all_files = {
    path.name
    for path in HOLDOUT_DIR.glob("*.wav")
}

expected_files = (
    AARON_FILES
    | BACKGROUND_FILES
    | EXCLUDE_FILES
)

if all_files != expected_files:
    print(
        "Expected:",
        sorted(expected_files),
    )

    print(
        "Found:",
        sorted(all_files),
    )

    raise SystemExit(
        "FAIL: holdout WAV set does not "
        "match expected 14 captures"
    )


print()
print(
    "PASS: exact 14-file holdout verified"
)

print(
    "Aaron:",
    len(AARON_FILES),
)

print(
    "Background:",
    len(BACKGROUND_FILES),
)

print(
    "Excluded wake residue:",
    len(EXCLUDE_FILES),
)


records = []

print()
print(
    "========== HOLDOUT V1 SCORES =========="
)


for path in sorted(
    HOLDOUT_DIR.glob("*.wav")
):
    if path.name in EXCLUDE_FILES:
        continue

    candidate = embed(
        model,
        path,
    )

    metadata = json.loads(
        (
            HOLDOUT_DIR
            / f"{path.stem}.json"
        ).read_text()
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
            long_score - tv_score,
            6,
        ),
        "short_margin": round(
            short_score - tv_score,
            6,
        ),
        "combined_margin": round(
            combined_score - tv_score,
            6,
        ),

        "best_profile_margin": round(
            max(
                long_score,
                short_score,
            )
            - tv_score,
            6,
        ),

        "best_top3_margin": round(
            max(
                long_top3,
                short_top3,
            )
            - tv_top3,
            6,
        ),

        "prior_short_margin_pass":
            (
                short_score - tv_score
                >= PRIOR_SHORT_MARGIN_MIDPOINT
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

    print(
        "  prior 0.175808:",
        "PASS"
        if record["prior_short_margin_pass"]
        else "REJECT",
    )


print()
print(
    "========== HOLDOUT SEPARATION =========="
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

    aaron_min = min(
        positives
    )

    background_max = max(
        negatives
    )

    gap = (
        aaron_min
        - background_max
    )

    midpoint = (
        aaron_min
        + background_max
    ) / 2.0

    separation[feature] = {
        "aaron_min": aaron_min,
        "background_max":
            background_max,
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
        f"{aaron_min:.6f}",
    )

    print(
        "  Background maximum: ",
        f"{background_max:.6f}",
    )

    print(
        "  Gap:                ",
        f"{gap:+.6f}",
    )

    print(
        "  Holdout midpoint:   ",
        f"{midpoint:.6f}",
    )

    print(
        "  Verdict:",
        "SEPARATED"
        if gap > 0
        else "OVERLAP",
    )


print()
print(
    "========== PRIOR THRESHOLD CHECK =========="
)

for record in records:
    if record["label"] != "AARON":
        continue

    print(
        repr(record["transcript"]),
        "margin=",
        f"{record['short_margin']:.6f}",
        "=>",
        "PASS"
        if record["prior_short_margin_pass"]
        else "FALSE REJECT",
    )


background_false_accepts = [
    record
    for record in records
    if (
        record["label"] == "BACKGROUND"
        and record[
            "prior_short_margin_pass"
        ]
    )
]

print()

print(
    "Background false accepts at "
    "prior midpoint:",
    len(background_false_accepts),
)


OUTPUT.parent.mkdir(
    parents=True,
    exist_ok=True,
)

OUTPUT.write_text(
    json.dumps(
        {
            "gate_enabled": False,
            "dataset": "holdout-v1",
            "aaron_count":
                len(AARON_FILES),
            "background_count":
                len(BACKGROUND_FILES),
            "excluded_wake_count":
                len(EXCLUDE_FILES),
            "prior_short_margin_midpoint":
                PRIOR_SHORT_MARGIN_MIDPOINT,
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
    for feature, result
    in separation.items()
    if result["gap"] > 0
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
        "NO FEATURE FULLY SEPARATES HOLDOUT"
    )

print()
print(
    "SPEAKER GATE REMAINS DISABLED"
)
