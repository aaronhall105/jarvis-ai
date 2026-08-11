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

DATA_DIR = Path("/data")

REFERENCE_DIR = Path("/reference")

TV_BASELINE = Path(
    "/tv-baseline/results/"
    "tv-negative-scores.json"
)

MODEL_DIR = Path(
    "/models/spkrec-ecapa-voxceleb"
)

OUTPUT = Path(
    "/output/mixed-scores.json"
)

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
            f"{path.name}: bad audio shape "
            f"{audio.shape}"
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


calibration = json.loads(
    (
        REFERENCE_DIR /
        "calibration.json"
    ).read_text()
)

positive_min = float(
    calibration[
        "positive_distribution"
    ]["min"]
)

tv_data = json.loads(
    TV_BASELINE.read_text()
)

all_tv_scores = [
    float(record["score_to_aaron"])
    for record in tv_data["records"]
]

if not all_tv_scores:
    raise SystemExit(
        "FAIL: TV baseline contains no scores"
    )

tv_max_all = max(all_tv_scores)

provisional_threshold = (
    positive_min + tv_max_all
) / 2.0

print(
    "========== BASELINE =========="
)

print(
    "Aaron positive min:",
    f"{positive_min:.6f}",
)

print(
    "TV maximum (all durations):",
    f"{tv_max_all:.6f}",
)

print(
    "Observed conservative gap:",
    f"{positive_min - tv_max_all:.6f}",
)

print(
    "Provisional midpoint:",
    f"{provisional_threshold:.6f}",
)

reference = torch.from_numpy(
    np.load(
        REFERENCE_DIR /
        "aaron-reference-v1.npy"
    )
).float()

reference = F.normalize(
    reference,
    dim=0,
)

torch.set_num_threads(4)

print()
print(
    "========== LOAD MODEL =========="
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

records = []

for wav_path in sorted(
    DATA_DIR.glob("*.wav")
):
    duration = float(
        sf.info(wav_path).duration
    )

    waveform = load_audio(
        wav_path
    )

    started = time.perf_counter()

    with torch.inference_mode():
        embedding = (
            classifier
            .encode_batch(waveform)
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

    metadata_path = (
        DATA_DIR /
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
        "duration_seconds": round(
            duration,
            3,
        ),
        "transcript": transcript,
        "score_to_aaron": round(
            score,
            6,
        ),
        "provisional_pass": (
            score
            >= provisional_threshold
        ),
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
        item["score_to_aaron"],
    reverse=True,
)

print()
print(
    "========== MIXED SCORES HIGH -> LOW =========="
)

for record in records:
    decision = (
        "WOULD-PASS"
        if record["provisional_pass"]
        else "WOULD-REJECT"
    )

    print()
    print(
        f"{record['score_to_aaron']:.6f}"
        f" | {record['duration_seconds']:.3f}s"
        f" | {decision}"
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


OUTPUT.parent.mkdir(
    parents=True,
    exist_ok=True,
)

OUTPUT.write_text(
    json.dumps(
        {
            "gate_enabled": False,
            "positive_min":
                positive_min,
            "tv_max_all":
                tv_max_all,
            "provisional_threshold":
                provisional_threshold,
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
    "Gate enabled: False"
)

print(
    "Provisional threshold:",
    f"{provisional_threshold:.6f}",
)

print(
    "NOTE: WOULD-PASS / WOULD-REJECT "
    "are diagnostic only."
)
