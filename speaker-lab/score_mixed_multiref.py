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


ENROLL_DIR = Path("/enroll")
MIXED_DIR = Path("/mixed")
MODEL_DIR = Path("/models/spkrec-ecapa-voxceleb")
OUTPUT = Path("/output/multiref-scores.json")

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


def normalise_embedding(
    value: torch.Tensor,
) -> torch.Tensor:
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


def expected_label(
    transcript: str,
) -> str:
    text = transcript.casefold()

    aaron_phrases = (
        "tell me four short facts",
        "what time is it",
        "tell me one short fact",
    )

    if any(
        phrase in text
        for phrase in aaron_phrases
    ):
        return "AARON"

    return "BACKGROUND"


torch.set_num_threads(4)

print(
    "========== LOAD ECAPA =========="
)

started = time.perf_counter()

classifier = EncoderClassifier.from_hparams(
    source=(
        "speechbrain/"
        "spkrec-ecapa-voxceleb"
    ),
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
print(
    "========== BUILD 7 AARON REFERENCES =========="
)

reference_embeddings = {}

for wav_path in sorted(
    ENROLL_DIR.glob("*.wav")
):
    waveform = load_audio(
        wav_path
    )

    with torch.inference_mode():
        embedding = (
            classifier.encode_batch(
                waveform
            )
        )

    embedding = normalise_embedding(
        embedding
    )

    reference_embeddings[
        wav_path.name
    ] = embedding

    print(
        "PASS:",
        wav_path.name,
        "dimension=",
        embedding.numel(),
    )


if len(reference_embeddings) != 7:
    raise SystemExit(
        "FAIL: expected exactly 7 "
        f"enrollment references, found "
        f"{len(reference_embeddings)}"
    )


reference_names = list(
    reference_embeddings
)

reference_stack = torch.stack(
    [
        reference_embeddings[name]
        for name in reference_names
    ]
)

centroid = F.normalize(
    reference_stack.mean(dim=0),
    dim=0,
)


print()
print(
    "========== MIXED MULTI-REFERENCE SCORES =========="
)

records = []

for wav_path in sorted(
    MIXED_DIR.glob("*.wav")
):
    waveform = load_audio(
        wav_path
    )

    started = time.perf_counter()

    with torch.inference_mode():
        embedding = (
            classifier.encode_batch(
                waveform
            )
        )

    embedding = normalise_embedding(
        embedding
    )

    per_reference = {
        name: cosine(
            embedding,
            reference_embeddings[name],
        )
        for name in reference_names
    }

    scores = sorted(
        per_reference.values(),
        reverse=True,
    )

    centroid_score = cosine(
        embedding,
        centroid,
    )

    max_ref = scores[0]

    top2_mean = statistics.fmean(
        scores[:2]
    )

    top3_mean = statistics.fmean(
        scores[:3]
    )

    mean_ref = statistics.fmean(
        scores
    )

    median_ref = statistics.median(
        scores
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

    duration = float(
        sf.info(wav_path).duration
    )

    record = {
        "wav": wav_path.name,
        "transcript": transcript,
        "expected": expected_label(
            transcript
        ),
        "duration_seconds": round(
            duration,
            3,
        ),
        "centroid": round(
            centroid_score,
            6,
        ),
        "max_ref": round(
            max_ref,
            6,
        ),
        "top2_mean": round(
            top2_mean,
            6,
        ),
        "top3_mean": round(
            top3_mean,
            6,
        ),
        "mean_ref": round(
            mean_ref,
            6,
        ),
        "median_ref": round(
            median_ref,
            6,
        ),
        "per_reference": {
            name: round(
                score,
                6,
            )
            for name, score
            in per_reference.items()
        },
        "inference_ms": round(
            (
                time.perf_counter()
                - started
            ) * 1000,
            1,
        ),
    }

    records.append(record)


records.sort(
    key=lambda item:
        item["top3_mean"],
    reverse=True,
)


for record in records:
    print()

    print(
        record["expected"],
        "|",
        f"{record['duration_seconds']:.3f}s",
        "|",
        repr(record["transcript"]),
    )

    print(
        "  centroid :",
        f"{record['centroid']:.6f}",
    )

    print(
        "  max_ref  :",
        f"{record['max_ref']:.6f}",
    )

    print(
        "  top2_mean:",
        f"{record['top2_mean']:.6f}",
    )

    print(
        "  top3_mean:",
        f"{record['top3_mean']:.6f}",
    )

    print(
        "  median   :",
        f"{record['median_ref']:.6f}",
    )

    print(
        "  mean     :",
        f"{record['mean_ref']:.6f}",
    )

    print(
        "  references:"
    )

    ordered = sorted(
        record["per_reference"].items(),
        key=lambda pair: pair[1],
        reverse=True,
    )

    for name, score in ordered:
        print(
            "   ",
            f"{score:.6f}",
            name,
        )


print()
print(
    "========== FEATURE SEPARATION =========="
)

features = (
    "centroid",
    "max_ref",
    "top2_mean",
    "top3_mean",
    "median_ref",
    "mean_ref",
)

separation = {}

for feature in features:
    aaron = [
        record[feature]
        for record in records
        if record["expected"] == "AARON"
    ]

    background = [
        record[feature]
        for record in records
        if record["expected"] == "BACKGROUND"
    ]

    aaron_min = min(aaron)
    background_max = max(background)

    gap = (
        aaron_min
        - background_max
    )

    separation[feature] = {
        "aaron_min": aaron_min,
        "background_max":
            background_max,
        "gap": round(
            gap,
            6,
        ),
    }

    print()
    print(feature)

    print(
        "  Aaron minimum:     ",
        f"{aaron_min:.6f}",
    )

    print(
        "  Background maximum:",
        f"{background_max:.6f}",
    )

    print(
        "  Gap:               ",
        f"{gap:.6f}",
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
            "reference_count": 7,
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

positive_features = [
    feature
    for feature, result
    in separation.items()
    if result["gap"] > 0
]

if positive_features:
    print(
        "PROMISING FEATURES:",
        ", ".join(
            positive_features
        ),
    )
else:
    print(
        "NO SIMPLE MULTI-REFERENCE "
        "FEATURE FULLY SEPARATES "
        "THIS MIXED TEST"
    )

print()
print(
    "SPEAKER GATE REMAINS DISABLED"
)
