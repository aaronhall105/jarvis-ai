from __future__ import annotations

import statistics
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
TEST_DIR = Path("/test")
MODEL_DIR = Path(
    "/models/spkrec-ecapa-voxceleb"
)

TARGET_RATE = 16000


def load_audio(
    path: Path,
) -> torch.Tensor:

    audio, rate = sf.read(
        path,
        dtype="float32",
        always_2d=False,
    )

    audio = np.asarray(
        audio
    )

    if audio.ndim == 2:
        audio = audio.mean(
            axis=1
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


def embed_waveform(
    model,
    waveform,
):

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


def embed_file(
    model,
    path,
):
    return embed_waveform(
        model,
        load_audio(path),
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


def top3(
    candidate,
    refs,
):
    values = sorted(
        (
            cosine(
                candidate,
                ref,
            )
            for ref
            in refs.values()
        ),
        reverse=True,
    )

    return statistics.fmean(
        values[:3]
    )


def build_refs(
    model,
    root,
):
    return {
        path.name:
            embed_file(
                model,
                path,
            )
        for path in sorted(
            root.glob("*.wav")
        )
    }


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


long_c = centroid(
    long_refs
)

short_c = centroid(
    short_refs
)

tv_c = centroid(
    tv_refs
)


files = list(
    TEST_DIR.glob("*.wav")
)

if len(files) != 1:
    raise SystemExit(
        f"FAIL: expected 1 test WAV, "
        f"found {len(files)}"
    )


path = files[0]

waveform = load_audio(
    path
)

candidate = embed_waveform(
    model,
    waveform,
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


print()
print(
    "========== FULL UTTERANCE =========="
)

print(
    "File:",
    path.name,
)

print(
    "Duration:",
    round(
        waveform.shape[-1]
        / TARGET_RATE,
        3,
    ),
    "seconds",
)

print(
    "Long Aaron:",
    f"{long_score:.6f}",
)

print(
    "Short Aaron:",
    f"{short_score:.6f}",
)

print(
    "TV:",
    f"{tv_score:.6f}",
)

print(
    "Long margin:",
    f"{long_score - tv_score:+.6f}",
)

print(
    "Short margin:",
    f"{short_score - tv_score:+.6f}",
)

print(
    "Long top3:",
    f"{top3(candidate, long_refs):.6f}",
)

print(
    "Short top3:",
    f"{top3(candidate, short_refs):.6f}",
)

print(
    "TV top3:",
    f"{top3(candidate, tv_refs):.6f}",
)


print()
print(
    "========== SLIDING WINDOWS =========="
)

duration_samples = (
    waveform.shape[-1]
)

for window_seconds in (
    1.00,
    1.25,
):

    window = round(
        window_seconds
        * TARGET_RATE
    )

    step = round(
        0.25
        * TARGET_RATE
    )

    if duration_samples < window:
        continue

    rows = []

    for start in range(
        0,
        duration_samples
        - window
        + 1,
        step,
    ):

        end = (
            start
            + window
        )

        chunk = waveform[
            :,
            start:end,
        ]

        emb = embed_waveform(
            model,
            chunk,
        )

        ls = cosine(
            emb,
            long_c,
        )

        ss = cosine(
            emb,
            short_c,
        )

        ts = cosine(
            emb,
            tv_c,
        )

        rows.append(
            (
                start
                / TARGET_RATE,
                end
                / TARGET_RATE,
                ls,
                ss,
                ts,
            )
        )

    print()
    print(
        f"--- {window_seconds:.2f}s windows ---"
    )

    for (
        start,
        end,
        ls,
        ss,
        ts,
    ) in rows:

        print(
            f"{start:.2f}-{end:.2f}s",
            f"long={ls:.6f}",
            f"short={ss:.6f}",
            f"tv={ts:.6f}",
            f"long_margin={ls-ts:+.6f}",
            f"short_margin={ss-ts:+.6f}",
        )

    print(
        "Best long:",
        f"{max(r[2] for r in rows):.6f}",
    )

    print(
        "Best short:",
        f"{max(r[3] for r in rows):.6f}",
    )

    print(
        "Best long margin:",
        f"{max(r[2]-r[4] for r in rows):+.6f}",
    )

    print(
        "Best short margin:",
        f"{max(r[3]-r[4] for r in rows):+.6f}",
    )


print()
print(
    "========== RESULT =========="
)

print(
    "DIAGNOSTIC ONLY"
)

print(
    "NO THRESHOLDS CHANGED"
)

print(
    "SPEAKER GATE REMAINS DISABLED"
)
