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


AARON_DIR = Path("/aaron")
TV_DIR = Path("/tv")
MIXED_DIR = Path("/mixed")
MODEL_DIR = Path("/models/spkrec-ecapa-voxceleb")
OUTPUT = Path("/output/dualclass-scores.json")

TARGET_RATE = 16000


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
        torch
        .from_numpy(audio)
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


def embedding(
    classifier: EncoderClassifier,
    path: Path,
) -> torch.Tensor:
    waveform = load_audio(path)

    with torch.inference_mode():
        value = classifier.encode_batch(
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


def label_transcript(text: str) -> str:
    clean = (
        " ".join(
            str(text)
            .casefold()
            .replace("?", "")
            .replace(".", "")
            .replace(",", "")
            .split()
        )
    )

    if (
        "tell me four short facts" in clean
        or "what time is it" in clean
        or "tell me one short fact" in clean
    ):
        return "AARON"

    if clean == "jarvis":
        return "AMBIGUOUS"

    return "BACKGROUND"


torch.set_num_threads(4)

print("========== LOAD MODEL ==========")

started = time.perf_counter()

classifier = EncoderClassifier.from_hparams(
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


print()
print("========== BUILD AARON CLASS ==========")

aaron_refs = {}

for path in sorted(
    AARON_DIR.glob("*.wav")
):
    aaron_refs[path.name] = embedding(
        classifier,
        path,
    )

    print(
        "AARON:",
        path.name,
    )

if len(aaron_refs) != 7:
    raise SystemExit(
        "FAIL: expected 7 Aaron references, "
        f"found {len(aaron_refs)}"
    )


print()
print("========== BUILD TV CLASS ==========")

tv_refs = {}

for path in sorted(
    TV_DIR.glob("*.wav")
):
    tv_refs[path.name] = embedding(
        classifier,
        path,
    )

    print(
        "TV:",
        path.name,
    )

if len(tv_refs) < 5:
    raise SystemExit(
        "FAIL: too few TV references: "
        f"{len(tv_refs)}"
    )

print(
    "TV reference count:",
    len(tv_refs),
)


aaron_stack = torch.stack(
    list(
        aaron_refs.values()
    )
)

tv_stack = torch.stack(
    list(
        tv_refs.values()
    )
)

aaron_centroid = F.normalize(
    aaron_stack.mean(dim=0),
    dim=0,
)

tv_centroid = F.normalize(
    tv_stack.mean(dim=0),
    dim=0,
)


print()
print("========== SCORE MIXED SET ==========")

records = []

for wav_path in sorted(
    MIXED_DIR.glob("*.wav")
):
    candidate = embedding(
        classifier,
        wav_path,
    )

    aaron_scores = sorted(
        (
            cosine(
                candidate,
                ref,
            )
            for ref in aaron_refs.values()
        ),
        reverse=True,
    )

    tv_scores = sorted(
        (
            cosine(
                candidate,
                ref,
            )
            for ref in tv_refs.values()
        ),
        reverse=True,
    )

    aaron_centroid_score = cosine(
        candidate,
        aaron_centroid,
    )

    tv_centroid_score = cosine(
        candidate,
        tv_centroid,
    )

    aaron_max = max(
        aaron_scores
    )

    tv_max = max(
        tv_scores
    )

    aaron_top2 = statistics.fmean(
        aaron_scores[:2]
    )

    tv_top2 = statistics.fmean(
        tv_scores[:2]
    )

    aaron_top3 = statistics.fmean(
        aaron_scores[:3]
    )

    tv_top3 = statistics.fmean(
        tv_scores[:3]
    )

    metadata_path = (
        MIXED_DIR /
        f"{wav_path.stem}.json"
    )

    transcript = ""

    if metadata_path.exists():
        metadata = json.loads(
            metadata_path.read_text()
        )

        transcript = str(
            metadata.get(
                "transcript",
                "",
            )
        )

    record = {
        "wav": wav_path.name,
        "transcript": transcript,
        "expected": label_transcript(
            transcript
        ),
        "duration_seconds": round(
            float(
                sf.info(wav_path).duration
            ),
            3,
        ),

        "aaron_centroid": round(
            aaron_centroid_score,
            6,
        ),
        "tv_centroid": round(
            tv_centroid_score,
            6,
        ),
        "centroid_margin": round(
            aaron_centroid_score
            - tv_centroid_score,
            6,
        ),

        "aaron_max": round(
            aaron_max,
            6,
        ),
        "tv_max": round(
            tv_max,
            6,
        ),
        "nearest_margin": round(
            aaron_max
            - tv_max,
            6,
        ),

        "aaron_top2": round(
            aaron_top2,
            6,
        ),
        "tv_top2": round(
            tv_top2,
            6,
        ),
        "top2_margin": round(
            aaron_top2
            - tv_top2,
            6,
        ),

        "aaron_top3": round(
            aaron_top3,
            6,
        ),
        "tv_top3": round(
            tv_top3,
            6,
        ),
        "top3_margin": round(
            aaron_top3
            - tv_top3,
            6,
        ),
    }

    records.append(
        record
    )


records.sort(
    key=lambda item:
        item["top3_margin"],
    reverse=True,
)


for record in records:
    print()
    print(
        record["expected"],
        "|",
        f"{record['duration_seconds']:.3f}s",
        "|",
        repr(
            record["transcript"]
        ),
    )

    print(
        "  centroid:"
        f" Aaron={record['aaron_centroid']:.6f}"
        f" TV={record['tv_centroid']:.6f}"
        f" margin={record['centroid_margin']:.6f}"
    )

    print(
        "  nearest :"
        f" Aaron={record['aaron_max']:.6f}"
        f" TV={record['tv_max']:.6f}"
        f" margin={record['nearest_margin']:.6f}"
    )

    print(
        "  top2    :"
        f" Aaron={record['aaron_top2']:.6f}"
        f" TV={record['tv_top2']:.6f}"
        f" margin={record['top2_margin']:.6f}"
    )

    print(
        "  top3    :"
        f" Aaron={record['aaron_top3']:.6f}"
        f" TV={record['tv_top3']:.6f}"
        f" margin={record['top3_margin']:.6f}"
    )


print()
print("========== MARGIN SEPARATION ==========")

features = (
    "centroid_margin",
    "nearest_margin",
    "top2_margin",
    "top3_margin",
)

separation = {}

for feature in features:
    positives = [
        record[feature]
        for record in records
        if record["expected"] == "AARON"
    ]

    negatives = [
        record[feature]
        for record in records
        if record["expected"] == "BACKGROUND"
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

    threshold = (
        positive_min
        + negative_max
    ) / 2.0

    separation[feature] = {
        "aaron_min":
            positive_min,
        "background_max":
            negative_max,
        "gap":
            round(
                gap,
                6,
            ),
        "midpoint":
            round(
                threshold,
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
        f"{gap:.6f}",
    )

    print(
        "  Diagnostic midpoint:",
        f"{threshold:.6f}",
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
            "aaron_reference_count":
                len(aaron_refs),
            "tv_reference_count":
                len(tv_refs),
            "records":
                records,
            "separation":
                separation,
        },
        indent=2,
    )
    + "\n"
)


print()
print("========== RESULT ==========")

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
        "NO DUAL-CLASS FEATURE "
        "FULLY SEPARATES CURRENT TEST"
    )

print()
print(
    "SPEAKER GATE REMAINS DISABLED"
)
