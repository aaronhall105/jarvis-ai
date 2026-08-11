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
CLEAN_DIR = Path("/clean")
HOLDOUT_DIR = Path("/holdout")
TV_DIR = Path("/tv")
MODEL_DIR = Path("/models/spkrec-ecapa-voxceleb")
OUTPUT = Path("/output/fixed-long-threshold-audit.json")

TARGET_RATE = 16000

# LOCKED BEFORE THIS AUDIT.
# Derived only from Clean Mixed V2.
THRESHOLD = 0.287044


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
            f"{path.name}: bad audio shape {audio.shape}"
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
    model,
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
    left,
    right,
) -> float:

    return float(
        torch.dot(
            F.normalize(left, dim=0),
            F.normalize(right, dim=0),
        ).item()
    )


def transcript_for(
    path: Path,
) -> str:

    metadata_path = (
        path.parent /
        f"{path.stem}.json"
    )

    if not metadata_path.exists():
        return ""

    try:
        data = json.loads(
            metadata_path.read_text()
        )

        return str(
            data.get(
                "transcript",
                "",
            )
        )

    except Exception:
        return ""


torch.set_num_threads(4)

print(
    "========== LOAD MODEL =========="
)

model = EncoderClassifier.from_hparams(
    source="speechbrain/spkrec-ecapa-voxceleb",
    savedir=str(MODEL_DIR),
    run_opts={
        "device": "cpu",
    },
)


print(
    "========== BUILD LOCKED LONG PROFILE =========="
)

references = {}

for path in sorted(
    LONG_DIR.glob("*.wav")
):
    references[path.name] = embed(
        model,
        path,
    )

    print(
        "PASS:",
        path.name,
    )


if len(references) != 7:
    raise SystemExit(
        f"FAIL: expected 7 references, "
        f"found {len(references)}"
    )


reference_stack = torch.stack(
    list(references.values())
)

aaron_centroid = F.normalize(
    reference_stack.mean(dim=0),
    dim=0,
)


records = []


def score_file(
    dataset: str,
    label: str,
    path: Path,
):

    candidate = embed(
        model,
        path,
    )

    score = cosine(
        candidate,
        aaron_centroid,
    )

    predicted_aaron = (
        score >= THRESHOLD
    )

    if label == "AARON":
        correct = predicted_aaron

    else:
        correct = not predicted_aaron

    record = {
        "dataset": dataset,
        "label": label,
        "wav": path.name,
        "transcript":
            transcript_for(path),
        "duration_seconds": round(
            float(
                sf.info(path).duration
            ),
            3,
        ),
        "score": round(
            score,
            6,
        ),
        "threshold": THRESHOLD,
        "predicted":
            (
                "AARON"
                if predicted_aaron
                else "REJECT"
            ),
        "correct": correct,
    }

    records.append(
        record
    )


print()
print(
    "========== SCORE CLEAN MIXED V2 =========="
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

    score_file(
        "clean-mixed-v2",
        label,
        path,
    )


print(
    "========== SCORE HOLDOUT V1 =========="
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

    score_file(
        "holdout-v1",
        label,
        path,
    )


print(
    "========== SCORE TV-ONLY NEGATIVES =========="
)

for path in sorted(
    TV_DIR.glob("*.wav")
):

    score_file(
        "tv-negative-v1",
        "BACKGROUND",
        path,
    )


records.sort(
    key=lambda item:
        item["score"],
    reverse=True,
)


print()
print(
    "========== ALL RESULTS =========="
)

for record in records:

    verdict = (
        "PASS"
        if record["correct"]
        else "FAIL"
    )

    print()

    print(
        verdict,
        "|",
        record["dataset"],
        "|",
        record["label"],
    )

    print(
        " ",
        f"{record['score']:.6f}",
        ">="
        if record["score"] >= THRESHOLD
        else "<",
        f"{THRESHOLD:.6f}",
    )

    print(
        " ",
        f"{record['duration_seconds']:.3f}s",
        "|",
        repr(
            record["transcript"]
        ),
    )

    print(
        "  decision:",
        record["predicted"],
    )


aaron_records = [
    record
    for record in records
    if record["label"] == "AARON"
]

negative_records = [
    record
    for record in records
    if record["label"]
    in (
        "BACKGROUND",
        "WAKE",
    )
]


false_rejects = [
    record
    for record in aaron_records
    if not record["correct"]
]

false_accepts = [
    record
    for record in negative_records
    if not record["correct"]
]


print()
print(
    "========== FIXED THRESHOLD AUDIT =========="
)

print(
    "Locked threshold:",
    f"{THRESHOLD:.6f}",
)

print()

print(
    "Aaron samples:",
    len(aaron_records),
)

print(
    "Aaron accepted:",
    len(aaron_records)
    - len(false_rejects),
)

print(
    "False rejects:",
    len(false_rejects),
)

print()

print(
    "Negative/wake samples:",
    len(negative_records),
)

print(
    "Correctly rejected:",
    len(negative_records)
    - len(false_accepts),
)

print(
    "False accepts:",
    len(false_accepts),
)


if aaron_records:
    print()

    print(
        "Aaron minimum:",
        f"{min(r['score'] for r in aaron_records):.6f}",
    )


if negative_records:
    print(
        "Negative maximum:",
        f"{max(r['score'] for r in negative_records):.6f}",
    )


if false_rejects:
    print()
    print(
        "========== FALSE REJECTS =========="
    )

    for record in false_rejects:
        print(
            f"{record['score']:.6f}",
            repr(
                record["transcript"]
            ),
        )


if false_accepts:
    print()
    print(
        "========== FALSE ACCEPTS =========="
    )

    for record in false_accepts:
        print(
            f"{record['score']:.6f}",
            "|",
            record["dataset"],
            "|",
            repr(
                record["transcript"]
            ),
        )


OUTPUT.parent.mkdir(
    parents=True,
    exist_ok=True,
)

OUTPUT.write_text(
    json.dumps(
        {
            "gate_enabled": False,
            "threshold_locked":
                THRESHOLD,
            "records":
                records,
            "false_reject_count":
                len(false_rejects),
            "false_accept_count":
                len(false_accepts),
        },
        indent=2,
    )
    + "\n"
)


print()
print(
    "========== RESULT =========="
)

if (
    not false_rejects
    and not false_accepts
):
    print(
        "PASS: FIXED LONG THRESHOLD "
        "SEPARATES ALL TRUSTED DATA"
    )

else:
    print(
        "FAIL: FIXED LONG THRESHOLD "
        "DOES NOT PERFECTLY SEPARATE "
        "ALL TRUSTED DATA"
    )

print()
print(
    "SPEAKER GATE REMAINS DISABLED"
)
