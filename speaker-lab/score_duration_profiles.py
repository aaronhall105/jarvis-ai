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
OUTPUT = Path("/output/duration-profile-scores.json")

TARGET_RATE = 16000
SHORT_LIMIT_SECONDS = 3.5


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


def make_embedding(
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


def centroid(
    embeddings: dict[str, torch.Tensor],
) -> torch.Tensor:
    return F.normalize(
        torch.stack(
            list(embeddings.values())
        ).mean(dim=0),
        dim=0,
    )


def top3_score(
    candidate: torch.Tensor,
    references: dict[str, torch.Tensor],
) -> float:
    scores = sorted(
        (
            cosine(candidate, ref)
            for ref in references.values()
        ),
        reverse=True,
    )

    return statistics.fmean(
        scores[:3]
    )


def expected_label(
    transcript: str,
) -> str:
    text = (
        " ".join(
            transcript
            .casefold()
            .replace("?", "")
            .replace(".", "")
            .replace(",", "")
            .split()
        )
    )

    if (
        "tell me four short facts" in text
        or "what time is it" in text
        or "tell me one short fact" in text
    ):
        return "AARON"

    if text == "jarvis":
        return "AMBIGUOUS"

    return "BACKGROUND"


torch.set_num_threads(4)

print("========== LOAD ECAPA ==========")

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


def build_class(
    name: str,
    root: Path,
) -> dict[str, torch.Tensor]:

    result = {}

    print()
    print(
        f"========== BUILD {name} =========="
    )

    for path in sorted(
        root.glob("*.wav")
    ):
        result[path.name] = make_embedding(
            classifier,
            path,
        )

        print(
            "PASS:",
            path.name,
        )

    print(
        "Count:",
        len(result),
    )

    return result


long_refs = build_class(
    "LONG AARON PROFILE",
    LONG_DIR,
)

short_refs = build_class(
    "SHORT AARON PROFILE",
    SHORT_DIR,
)

tv_refs = build_class(
    "TV PROFILE",
    TV_DIR,
)


if len(long_refs) != 7:
    raise SystemExit(
        f"FAIL: expected 7 long Aaron refs, "
        f"found {len(long_refs)}"
    )

if len(short_refs) != 10:
    raise SystemExit(
        f"FAIL: expected 10 short Aaron refs, "
        f"found {len(short_refs)}"
    )

if len(tv_refs) < 5:
    raise SystemExit(
        f"FAIL: expected >=5 TV refs, "
        f"found {len(tv_refs)}"
    )


combined_refs = {
    **{
        f"long/{name}": value
        for name, value in long_refs.items()
    },
    **{
        f"short/{name}": value
        for name, value in short_refs.items()
    },
}


long_centroid = centroid(
    long_refs
)

short_centroid = centroid(
    short_refs
)

combined_centroid = centroid(
    combined_refs
)

tv_centroid = centroid(
    tv_refs
)


print()
print(
    "========== SCORE SAVED MIXED TEST =========="
)

records = []

for wav_path in sorted(
    MIXED_DIR.glob("*.wav")
):
    candidate = make_embedding(
        classifier,
        wav_path,
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

    long_score = cosine(
        candidate,
        long_centroid,
    )

    short_score = cosine(
        candidate,
        short_centroid,
    )

    combined_score = cosine(
        candidate,
        combined_centroid,
    )

    tv_score = cosine(
        candidate,
        tv_centroid,
    )

    long_top3 = top3_score(
        candidate,
        long_refs,
    )

    short_top3 = top3_score(
        candidate,
        short_refs,
    )

    tv_top3 = top3_score(
        candidate,
        tv_refs,
    )

    if duration <= SHORT_LIMIT_SECONDS:
        duration_profile = "SHORT"
        duration_aaron_score = short_score
        duration_top3 = short_top3
    else:
        duration_profile = "LONG"
        duration_aaron_score = long_score
        duration_top3 = long_top3

    best_aaron_score = max(
        long_score,
        short_score,
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

        "long_centroid": round(
            long_score,
            6,
        ),
        "short_centroid": round(
            short_score,
            6,
        ),
        "short_minus_long": round(
            short_score - long_score,
            6,
        ),

        "combined_centroid": round(
            combined_score,
            6,
        ),
        "tv_centroid": round(
            tv_score,
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
            best_aaron_score - tv_score,
            6,
        ),

        "long_top3": round(
            long_top3,
            6,
        ),
        "short_top3": round(
            short_top3,
            6,
        ),
        "tv_top3": round(
            tv_top3,
            6,
        ),
        "short_top3_margin": round(
            short_top3 - tv_top3,
            6,
        ),

        "duration_profile":
            duration_profile,

        "duration_aaron_score": round(
            duration_aaron_score,
            6,
        ),
        "duration_margin": round(
            duration_aaron_score
            - tv_score,
            6,
        ),
        "duration_top3_margin": round(
            duration_top3
            - tv_top3,
            6,
        ),
    }

    records.append(
        record
    )


records.sort(
    key=lambda item:
        item["duration_margin"],
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
        "  long centroid :",
        f"{record['long_centroid']:.6f}",
    )

    print(
        "  short centroid:",
        f"{record['short_centroid']:.6f}",
        " delta=",
        f"{record['short_minus_long']:+.6f}",
    )

    print(
        "  combined      :",
        f"{record['combined_centroid']:.6f}",
    )

    print(
        "  TV centroid   :",
        f"{record['tv_centroid']:.6f}",
    )

    print(
        "  short margin  :",
        f"{record['short_margin']:.6f}",
    )

    print(
        "  combined margin:",
        f"{record['combined_margin']:.6f}",
    )

    print(
        "  duration profile:",
        record["duration_profile"],
    )

    print(
        "  duration margin :",
        f"{record['duration_margin']:.6f}",
    )

    print(
        "  short top3 margin:",
        f"{record['short_top3_margin']:.6f}",
    )


print()
print(
    "========== PROFILE SEPARATION =========="
)

features = (
    "long_centroid",
    "short_centroid",
    "combined_centroid",
    "short_margin",
    "combined_margin",
    "best_profile_margin",
    "short_top3_margin",
    "duration_margin",
    "duration_top3_margin",
)

separation = {}

for feature in features:

    positives = [
        record[feature]
        for record in records
        if record["expected"]
        == "AARON"
    ]

    negatives = [
        record[feature]
        for record in records
        if record["expected"]
        == "BACKGROUND"
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
        f"{gap:.6f}",
    )

    print(
        "  Diagnostic midpoint:",
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
            "short_limit_seconds":
                SHORT_LIMIT_SECONDS,
            "long_reference_count":
                len(long_refs),
            "short_reference_count":
                len(short_refs),
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
print(
    "========== CRITICAL COMMAND =========="
)

time_records = [
    record
    for record in records
    if (
        record["expected"] == "AARON"
        and "what time is it"
        in record["transcript"].casefold()
    )
]

for record in time_records:
    print(
        "'What time is it?'"
    )

    print(
        "  long:",
        f"{record['long_centroid']:.6f}",
    )

    print(
        "  short:",
        f"{record['short_centroid']:.6f}",
    )

    print(
        "  improvement:",
        f"{record['short_minus_long']:+.6f}",
    )

    print(
        "  TV:",
        f"{record['tv_centroid']:.6f}",
    )

    print(
        "  short margin:",
        f"{record['short_margin']:.6f}",
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
        "NO PROFILE FEATURE "
        "FULLY SEPARATES CURRENT MIXED TEST"
    )

print()
print(
    "SPEAKER GATE REMAINS DISABLED"
)
