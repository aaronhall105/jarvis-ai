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

from speechbrain.inference.speaker import (
    EncoderClassifier,
)


NEGATIVE_DIR = Path("/negative")

REFERENCE_PATH = Path(
    "/reference/aaron-reference-v1.npy"
)

MODEL_DIR = Path(
    "/models/spkrec-ecapa-voxceleb"
)

OUTPUT_PATH = Path(
    "/output/tv-negative-scores.json"
)

TARGET_RATE = 16000
MIN_ANALYSIS_SECONDS = 1.5


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
        audio = audio.mean(axis=1)

    if audio.ndim != 1:
        raise RuntimeError(
            f"{path.name}: bad shape "
            f"{audio.shape}"
        )

    waveform = (
        torch
        .from_numpy(audio)
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


def cosine(
    left: torch.Tensor,
    right: torch.Tensor,
) -> float:

    left = F.normalize(
        left,
        dim=0,
    )

    right = F.normalize(
        right,
        dim=0,
    )

    return float(
        torch.dot(
            left,
            right,
        ).item()
    )


torch.set_num_threads(4)

reference = torch.from_numpy(
    np.load(
        REFERENCE_PATH
    )
).float()

reference = F.normalize(
    reference,
    dim=0,
)

print(
    "========== LOAD MODEL =========="
)

started = time.perf_counter()

classifier = EncoderClassifier.from_hparams(
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

records = []

for wav_path in sorted(
    NEGATIVE_DIR.glob("*.wav")
):
    info = sf.info(
        wav_path
    )

    duration = float(
        info.duration
    )

    waveform = load_audio(
        wav_path
    )

    started = time.perf_counter()

    with torch.inference_mode():
        embedding = (
            classifier.encode_batch(
                waveform
            )
            .squeeze()
            .detach()
            .cpu()
            .float()
        )

    embedding = F.normalize(
        embedding,
        dim=0,
    )

    score = cosine(
        embedding,
        reference,
    )

    json_path = (
        NEGATIVE_DIR
        / f"{wav_path.stem}.json"
    )

    transcript = ""

    if json_path.exists():
        try:
            metadata = json.loads(
                json_path.read_text()
            )

            transcript = str(
                metadata.get(
                    "transcript",
                    "",
                )
            )

        except Exception:
            pass

    records.append(
        {
            "wav": wav_path.name,
            "duration_seconds": round(
                duration,
                3,
            ),
            "score_to_aaron": round(
                score,
                6,
            ),
            "transcript": transcript,
            "inference_ms": round(
                (
                    time.perf_counter()
                    - started
                )
                * 1000,
                1,
            ),
        }
    )


records.sort(
    key=lambda item: item[
        "score_to_aaron"
    ],
    reverse=True,
)


print()
print(
    "========== TV SCORES "
    "HIGH -> LOW =========="
)

for record in records:
    marker = (
        "ANALYSE"
        if record[
            "duration_seconds"
        ]
        >= MIN_ANALYSIS_SECONDS
        else "SHORT"
    )

    print()
    print(
        f"{record['score_to_aaron']:.6f}"
        f" | {record['duration_seconds']:.3f}s"
        f" | {marker}"
    )

    print(
        "  transcript:",
        repr(
            record["transcript"]
        ),
    )

    print(
        "  wav:",
        record["wav"],
    )


analysis_scores = [
    record["score_to_aaron"]
    for record in records
    if record["duration_seconds"]
    >= MIN_ANALYSIS_SECONDS
]


summary = {
    "reference": "Aaron V1",
    "gate_enabled": False,
    "minimum_analysis_seconds":
        MIN_ANALYSIS_SECONDS,
    "segment_count": len(records),
    "analysis_segment_count":
        len(analysis_scores),
    "records": records,
}


print()
print(
    "========== NEGATIVE DISTRIBUTION =========="
)

if analysis_scores:

    summary[
        "negative_distribution"
    ] = {
        "min": round(
            min(analysis_scores),
            6,
        ),
        "median": round(
            statistics.median(
                analysis_scores
            ),
            6,
        ),
        "mean": round(
            statistics.fmean(
                analysis_scores
            ),
            6,
        ),
        "max": round(
            max(analysis_scores),
            6,
        ),
    }

    for key, value in summary[
        "negative_distribution"
    ].items():

        print(
            f"negative_{key}:",
            value,
        )

else:
    summary[
        "negative_distribution"
    ] = None

    print(
        "FAIL: no >=1.5 second "
        "negative samples"
    )


OUTPUT_PATH.parent.mkdir(
    parents=True,
    exist_ok=True,
)

OUTPUT_PATH.write_text(
    json.dumps(
        summary,
        indent=2,
    )
    + "\n"
)


print()
print(
    "========== RESULT =========="
)

print(
    "Aaron positive min:    0.724365"
)

print(
    "Aaron positive median: 0.794624"
)

if analysis_scores:

    negative_max = max(
        analysis_scores
    )

    margin = (
        0.724365
        - negative_max
    )

    print(
        "TV negative max:     ",
        f"{negative_max:.6f}",
    )

    print(
        "Observed gap:        ",
        f"{margin:.6f}",
    )

print()
print(
    "OBSERVE ONLY: "
    "NO SPEAKER GATE ENABLED"
)
