from __future__ import annotations

import csv
import json
import os
import statistics
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
import torch.nn.functional as F
import torchaudio
from speechbrain.inference.speaker import EncoderClassifier


DATA_DIR = Path("/data")
MODEL_DIR = Path("/models/spkrec-ecapa-voxceleb")
OUTPUT_DIR = Path("/output")

EXPECTED_COUNT = 7
TARGET_RATE = 16_000


def load_audio(path: Path) -> torch.Tensor:
    audio, sample_rate = sf.read(
        path,
        dtype="float32",
        always_2d=False,
    )

    audio = np.asarray(audio)

    if audio.ndim == 2:
        audio = audio.mean(axis=1)

    if audio.ndim != 1:
        raise RuntimeError(
            f"{path.name}: unexpected audio shape "
            f"{audio.shape}"
        )

    waveform = torch.from_numpy(audio).float().unsqueeze(0)

    if sample_rate != TARGET_RATE:
        waveform = torchaudio.functional.resample(
            waveform,
            sample_rate,
            TARGET_RATE,
        )

    if waveform.numel() == 0:
        raise RuntimeError(
            f"{path.name}: empty waveform"
        )

    return waveform


def cosine(
    a: torch.Tensor,
    b: torch.Tensor,
) -> float:
    return float(
        torch.dot(
            F.normalize(a, dim=0),
            F.normalize(b, dim=0),
        ).item()
    )


threads = max(
    1,
    min(
        4,
        os.cpu_count() or 1,
    ),
)

torch.set_num_threads(threads)

print("========== SPEAKER LAB ==========")
print("torch:", torch.__version__)
print("torchaudio:", torchaudio.__version__)

try:
    import speechbrain

    print(
        "speechbrain:",
        getattr(
            speechbrain,
            "__version__",
            "unknown",
        ),
    )
except Exception:
    print("speechbrain: imported")

print("CPU threads:", threads)
print("CUDA available:", torch.cuda.is_available())

files = sorted(
    DATA_DIR.glob("*.wav")
)

print()
print("Enrollment WAV files:", len(files))

if len(files) != EXPECTED_COUNT:
    raise SystemExit(
        f"FAIL: expected {EXPECTED_COUNT} WAVs, "
        f"found {len(files)}"
    )

print()
print("========== LOAD ECAPA MODEL ==========")

model_started = time.perf_counter()

classifier = EncoderClassifier.from_hparams(
    source="speechbrain/spkrec-ecapa-voxceleb",
    savedir=str(MODEL_DIR),
    run_opts={
        "device": "cpu",
    },
)

model_ms = (
    time.perf_counter()
    - model_started
) * 1000.0

print(
    "PASS: ECAPA model loaded"
)
print(
    "Model load ms:",
    round(model_ms, 1),
)

print()
print("========== EXTRACT EMBEDDINGS ==========")

embeddings: dict[str, torch.Tensor] = {}
durations: dict[str, float] = {}
inference_ms: dict[str, float] = {}

for path in files:
    info = sf.info(path)

    durations[path.name] = float(
        info.duration
    )

    waveform = load_audio(path)

    started = time.perf_counter()

    with torch.inference_mode():
        embedding = classifier.encode_batch(
            waveform
        )

    elapsed_ms = (
        time.perf_counter()
        - started
    ) * 1000.0

    embedding = (
        embedding
        .squeeze()
        .detach()
        .cpu()
        .float()
    )

    embedding = F.normalize(
        embedding,
        dim=0,
    )

    if not torch.isfinite(
        embedding
    ).all():
        raise RuntimeError(
            f"{path.name}: non-finite embedding"
        )

    embeddings[path.name] = embedding
    inference_ms[path.name] = elapsed_ms

    print()
    print("FILE:", path.name)
    print(
        "input_duration_seconds:",
        round(
            durations[path.name],
            3,
        ),
    )
    print(
        "embedding_dimension:",
        embedding.numel(),
    )
    print(
        "embedding_norm:",
        round(
            float(
                torch.linalg.vector_norm(
                    embedding
                ).item()
            ),
            6,
        ),
    )
    print(
        "inference_ms:",
        round(
            elapsed_ms,
            1,
        ),
    )


names = list(
    embeddings.keys()
)

matrix = []

for left in names:
    row = []

    for right in names:
        row.append(
            cosine(
                embeddings[left],
                embeddings[right],
            )
        )

    matrix.append(row)


print()
print("========== PAIRWISE COSINE MATRIX ==========")

header = (
    "sample".ljust(27)
    + " "
    + " ".join(
        f"{index + 1:>7}"
        for index in range(len(names))
    )
)

print(header)

for index, name in enumerate(names):
    scores = " ".join(
        f"{score:7.3f}"
        for score in matrix[index]
    )

    print(
        f"{index + 1}: "
        f"{name[:23].ljust(23)} "
        f"{scores}"
    )


print()
print("========== LEAVE-ONE-OUT AARON SCORES ==========")

loo_scores: dict[str, float] = {}

for index, name in enumerate(names):
    others = [
        embeddings[other]
        for other in names
        if other != name
    ]

    centroid = torch.stack(
        others
    ).mean(dim=0)

    centroid = F.normalize(
        centroid,
        dim=0,
    )

    score = cosine(
        embeddings[name],
        centroid,
    )

    loo_scores[name] = score

    print(
        f"{name}: {score:.6f}"
    )


positive_scores = list(
    loo_scores.values()
)

minimum = min(
    positive_scores
)

maximum = max(
    positive_scores
)

median = statistics.median(
    positive_scores
)

mean = statistics.fmean(
    positive_scores
)

print()
print("========== POSITIVE DISTRIBUTION ==========")
print(
    "positive_min:",
    round(minimum, 6),
)
print(
    "positive_median:",
    round(median, 6),
)
print(
    "positive_mean:",
    round(mean, 6),
)
print(
    "positive_max:",
    round(maximum, 6),
)


print()
print("========== CREATE AARON REFERENCE ==========")

reference = torch.stack(
    [
        embeddings[name]
        for name in names
    ]
).mean(dim=0)

reference = F.normalize(
    reference,
    dim=0,
)

OUTPUT_DIR.mkdir(
    parents=True,
    exist_ok=True,
)

reference_path = (
    OUTPUT_DIR
    / "aaron-reference-v1.npy"
)

np.save(
    reference_path,
    reference.numpy(),
)

metadata = {
    "speaker": "Aaron",
    "version": 1,
    "model": (
        "speechbrain/"
        "spkrec-ecapa-voxceleb"
    ),
    "model_sample_rate": TARGET_RATE,
    "embedding_dimension": int(
        reference.numel()
    ),
    "sample_count": len(names),
    "samples": [
        {
            "wav": name,
            "duration_seconds": round(
                durations[name],
                3,
            ),
            "leave_one_out_score": round(
                loo_scores[name],
                6,
            ),
            "inference_ms": round(
                inference_ms[name],
                1,
            ),
        }
        for name in names
    ],
    "positive_distribution": {
        "min": round(
            minimum,
            6,
        ),
        "median": round(
            median,
            6,
        ),
        "mean": round(
            mean,
            6,
        ),
        "max": round(
            maximum,
            6,
        ),
    },
    "gate_enabled": False,
    "threshold": None,
}

(
    OUTPUT_DIR
    / "calibration.json"
).write_text(
    json.dumps(
        metadata,
        indent=2,
    )
    + "\n"
)

with (
    OUTPUT_DIR
    / "pairwise.csv"
).open(
    "w",
    newline="",
) as handle:
    writer = csv.writer(
        handle
    )

    writer.writerow(
        ["sample"] + names
    )

    for name, row in zip(
        names,
        matrix,
    ):
        writer.writerow(
            [name]
            + [
                f"{value:.8f}"
                for value in row
            ]
        )

print(
    "Reference:",
    reference_path,
)

print(
    "Reference norm:",
    round(
        float(
            torch.linalg.vector_norm(
                reference
            ).item()
        ),
        6,
    ),
)

print()
print("========== SPEAKER CALIBRATION RESULT ==========")

if minimum < 0.0:
    print(
        "FAIL: at least one enrollment sample "
        "is strongly inconsistent"
    )
    raise SystemExit(1)

print(
    "PASS: all seven Aaron enrollment "
    "samples embedded successfully"
)

print(
    "PASS: Aaron reference embedding created"
)

print(
    "OBSERVE ONLY: speaker gate remains DISABLED"
)

print(
    "NEXT: collect TV/non-Aaron negative samples "
    "before selecting any threshold"
)
