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
LIVE_DIR = Path("/live")
MODEL_DIR = Path(
    "/models/spkrec-ecapa-voxceleb"
)
OUTPUT = Path(
    "/output/best-profile-v1.json"
)

TARGET_RATE = 16000

# Existing observe-only bands.
# LOCKED for this audit.
STRONG = 0.340000
BACKGROUND = 0.270000


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


def load_audio(
    path: Path,
) -> torch.Tensor:

    audio, rate = sf.read(
        path,
        dtype="float32",
        always_2d=False,
    )

    audio = np.asarray(audio)

    if audio.ndim == 2:
        audio = audio.mean(
            axis=1
        )

    if audio.ndim != 1:
        raise RuntimeError(
            f"{path.name}: "
            f"bad shape {audio.shape}"
        )

    waveform = (
        torch.from_numpy(audio)
        .float()
        .unsqueeze(0)
    )

    if rate != TARGET_RATE:
        waveform = (
            torchaudio.functional.resample(
                waveform,
                rate,
                TARGET_RATE,
            )
        )

    return waveform


def embed(
    model,
    path,
):

    waveform = load_audio(
        path
    )

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
    left,
    right,
):

    return float(
        torch.dot(
            F.normalize(
                left,
                dim=0,
            ),
            F.normalize(
                right,
                dim=0,
            ),
        ).item()
    )


def build_refs(
    model,
    root,
):

    return {
        path.name:
            embed(
                model,
                path,
            )
        for path in sorted(
            root.glob("*.wav")
        )
    }


def centroid(
    refs,
):

    return F.normalize(
        torch.stack(
            list(
                refs.values()
            )
        ).mean(
            dim=0
        ),
        dim=0,
    )


def transcript_for(
    path,
):

    metadata = (
        path.parent
        / f"{path.stem}.json"
    )

    if not metadata.exists():
        return ""

    try:
        data = json.loads(
            metadata.read_text()
        )

        return str(
            data.get(
                "transcript",
                "",
            )
        )

    except Exception:
        return ""


def classify(
    score,
):

    if score >= STRONG:
        return "STRONG_AARON"

    if score <= BACKGROUND:
        return "BACKGROUND"

    return "AMBIGUOUS"


torch.set_num_threads(4)

print(
    "========== LOAD ECAPA =========="
)

model = EncoderClassifier.from_hparams(
    source=(
        "speechbrain/"
        "spkrec-ecapa-voxceleb"
    ),
    savedir=str(
        MODEL_DIR
    ),
    run_opts={
        "device": "cpu",
    },
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


def score(
    dataset,
    label,
    path,
):

    candidate = embed(
        model,
        path,
    )

    long_score = cosine(
        candidate,
        long_c,
    )

    short_score = cosine(
        candidate,
        short_c,
    )

    tv_score = cosine(
        candidate,
        tv_c,
    )

    best = max(
        long_score,
        short_score,
    )

    record = {
        "dataset":
            dataset,
        "label":
            label,
        "wav":
            path.name,
        "transcript":
            transcript_for(path),
        "long":
            round(
                long_score,
                6,
            ),
        "short":
            round(
                short_score,
                6,
            ),
        "best_profile":
            round(
                best,
                6,
            ),
        "tv":
            round(
                tv_score,
                6,
            ),
        "best_margin":
            round(
                best - tv_score,
                6,
            ),
        "long_classification":
            classify(
                long_score
            ),
        "best_classification":
            classify(
                best
            ),
    }

    records.append(
        record
    )


for path in sorted(
    CLEAN_DIR.glob("*.wav")
):

    if path.name in CLEAN_AARON:
        label = "AARON"

    elif path.name in CLEAN_BACKGROUND:
        label = "BACKGROUND"

    else:
        continue

    score(
        "clean-v2",
        label,
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

    score(
        "holdout-v1",
        label,
        path,
    )


for path in sorted(
    TV_DIR.glob("*.wav")
):

    score(
        "tv-negative-v1",
        "BACKGROUND",
        path,
    )


live_files = sorted(
    LIVE_DIR.glob("*.wav")
)

if len(live_files) != 1:
    raise SystemExit(
        f"FAIL: expected exactly "
        f"1 live diagnostic WAV, "
        f"found {len(live_files)}"
    )

score(
    "live-tv-false-negative",
    "AARON",
    live_files[0],
)


print()
print(
    "========== BEST PROFILE AUDIT =========="
)


for record in sorted(
    records,
    key=lambda item:
        item["best_profile"],
    reverse=True,
):

    print()

    print(
        record["label"],
        "|",
        record["dataset"],
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
        "  best:",
        f"{record['best_profile']:.6f}",
    )

    print(
        "  TV:",
        f"{record['tv']:.6f}",
    )

    print(
        "  margin:",
        f"{record['best_margin']:+.6f}",
    )

    print(
        "  long-only:",
        record[
            "long_classification"
        ],
    )

    print(
        "  best-profile:",
        record[
            "best_classification"
        ],
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


long_false_background = [
    r
    for r in aaron
    if r[
        "long_classification"
    ] == "BACKGROUND"
]

best_false_background = [
    r
    for r in aaron
    if r[
        "best_classification"
    ] == "BACKGROUND"
]

negative_strong = [
    r
    for r in negative
    if r[
        "best_classification"
    ] == "STRONG_AARON"
]

negative_ambiguous = [
    r
    for r in negative
    if r[
        "best_classification"
    ] == "AMBIGUOUS"
]


print()
print(
    "========== SUMMARY =========="
)

print(
    "Aaron samples:",
    len(aaron),
)

print(
    "Long-only Aaron classified BACKGROUND:",
    len(
        long_false_background
    ),
)

print(
    "Best-profile Aaron classified BACKGROUND:",
    len(
        best_false_background
    ),
)

print()

print(
    "Negative/wake samples:",
    len(negative),
)

print(
    "Best-profile negatives STRONG_AARON:",
    len(
        negative_strong
    ),
)

print(
    "Best-profile negatives AMBIGUOUS:",
    len(
        negative_ambiguous
    ),
)

print()

print(
    "Aaron minimum best-profile:",
    f"{min(r['best_profile'] for r in aaron):.6f}",
)

print(
    "Negative maximum best-profile:",
    f"{max(r['best_profile'] for r in negative):.6f}",
)


if negative_strong:
    print()
    print(
        "========== NEGATIVE STRONG FALSE ACCEPTS =========="
    )

    for r in negative_strong:
        print(
            f"{r['best_profile']:.6f}",
            "|",
            r["dataset"],
            "|",
            repr(
                r["transcript"]
            ),
        )


if best_false_background:
    print()
    print(
        "========== AARON BACKGROUND FALSE REJECTS =========="
    )

    for r in best_false_background:
        print(
            f"{r['best_profile']:.6f}",
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
            "strong_band":
                STRONG,
            "background_band":
                BACKGROUND,
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
    "PRODUCTION CLASSIFICATION UNCHANGED"
)

print(
    "SPEAKER GATE REMAINS DISABLED"
)
