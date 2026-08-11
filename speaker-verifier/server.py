from __future__ import annotations

import json
import os
import threading
import time
from http.server import (
    BaseHTTPRequestHandler,
    ThreadingHTTPServer,
)
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
import torch.nn.functional as F
import torchaudio

from speechbrain.inference.speaker import EncoderClassifier


HOST = "0.0.0.0"
PORT = int(
    os.getenv(
        "JARVIS_SPEAKER_PORT",
        "8091",
    )
)

ENROLL_DIR = Path(
    os.getenv(
        "JARVIS_SPEAKER_ENROLL_DIR",
        "/enroll",
    )
)

SHORT_ENROLL_DIR = Path(
    os.getenv(
        "JARVIS_SPEAKER_SHORT_ENROLL_DIR",
        "/short-enroll",
    )
)

MODEL_DIR = Path(
    os.getenv(
        "JARVIS_SPEAKER_MODEL_DIR",
        "/models/spkrec-ecapa-voxceleb",
    )
)

MODEL_SOURCE = (
    "speechbrain/"
    "spkrec-ecapa-voxceleb"
)

MODEL_RATE = 16000
INPUT_RATE = 24000

SAMPLE_WIDTH = 2
CHANNELS = 1

STRONG_AARON_THRESHOLD = 0.340000
BACKGROUND_THRESHOLD = 0.270000

MAX_PCM_BYTES = 4 * 1024 * 1024

MODEL_LOCK = threading.Lock()


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


def load_reference_audio(
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
            f"{path.name}: "
            f"bad shape {audio.shape}"
        )

    waveform = (
        torch
        .from_numpy(audio)
        .float()
        .unsqueeze(0)
    )

    if rate != MODEL_RATE:
        waveform = torchaudio.functional.resample(
            waveform,
            rate,
            MODEL_RATE,
        )

    return waveform


def pcm24k_to_waveform(
    pcm: bytes,
) -> torch.Tensor:
    if not pcm:
        raise ValueError(
            "empty PCM"
        )

    if len(pcm) % SAMPLE_WIDTH:
        raise ValueError(
            "PCM byte count is not aligned "
            "to 16-bit samples"
        )

    audio = np.frombuffer(
        pcm,
        dtype="<i2",
    )

    if audio.size == 0:
        raise ValueError(
            "no PCM samples"
        )

    float_audio = (
        audio
        .astype(
            np.float32,
            copy=True,
        )
        / 32768.0
    )

    waveform = (
        torch
        .from_numpy(
            float_audio
        )
        .float()
        .unsqueeze(0)
    )

    waveform = torchaudio.functional.resample(
        waveform,
        INPUT_RATE,
        MODEL_RATE,
    )

    return waveform


def cosine(
    left: torch.Tensor,
    right: torch.Tensor,
) -> float:
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


def classify(
    score: float,
) -> str:
    if score >= STRONG_AARON_THRESHOLD:
        return "STRONG_AARON"

    if score <= BACKGROUND_THRESHOLD:
        return "BACKGROUND"

    return "AMBIGUOUS"


torch.set_num_threads(4)

print(
    "========== JARVIS SPEAKER VERIFIER ==========",
    flush=True,
)

started = time.perf_counter()

classifier = EncoderClassifier.from_hparams(
    source=MODEL_SOURCE,
    savedir=str(MODEL_DIR),
    run_opts={
        "device": "cpu",
    },
)

print(
    "PASS: ECAPA loaded in",
    round(
        (
            time.perf_counter()
            - started
        ) * 1000,
        1,
    ),
    "ms",
    flush=True,
)


reference_embeddings = {}

for wav_path in sorted(
    ENROLL_DIR.glob("*.wav")
):
    waveform = load_reference_audio(
        wav_path
    )

    with torch.inference_mode():
        value = classifier.encode_batch(
            waveform
        )

    reference_embeddings[
        wav_path.name
    ] = normalise_embedding(
        value
    )

    print(
        "PASS: enrolled",
        wav_path.name,
        flush=True,
    )


if len(reference_embeddings) != 7:
    raise RuntimeError(
        "Expected exactly 7 Aaron "
        "long-profile references; found "
        f"{len(reference_embeddings)}"
    )


reference_stack = torch.stack(
    list(
        reference_embeddings.values()
    )
)

aaron_centroid = F.normalize(
    reference_stack.mean(
        dim=0
    ),
    dim=0,
)


short_reference_embeddings = {}

for wav_path in sorted(
    SHORT_ENROLL_DIR.glob("*.wav")
):
    waveform = load_reference_audio(
        wav_path
    )

    with torch.inference_mode():
        value = classifier.encode_batch(
            waveform
        )

    short_reference_embeddings[
        wav_path.name
    ] = normalise_embedding(
        value
    )

    print(
        "PASS: short enrolled",
        wav_path.name,
        flush=True,
    )


if len(short_reference_embeddings) != 10:
    raise RuntimeError(
        "Expected exactly 10 Aaron "
        "short-profile references; found "
        f"{len(short_reference_embeddings)}"
    )


short_reference_stack = torch.stack(
    list(
        short_reference_embeddings.values()
    )
)

short_aaron_centroid = F.normalize(
    short_reference_stack.mean(
        dim=0
    ),
    dim=0,
)


ready_ms = round(
    (
        time.perf_counter()
        - started
    ) * 1000,
    1,
)

print(
    "PASS: verifier ready",
    f"references={len(reference_embeddings)}",
    f"startup_ms={ready_ms}",
    flush=True,
)


WINDOW_SHORT_MODEL_RATE = 16000
WINDOW_SHORT_SECONDS = 1.00
WINDOW_SHORT_STEP_SECONDS = 0.25

# Locked from window-evidence-v1.
# Prospective observation only.
WINDOW_SHORT_CANDIDATE_THRESHOLD = 0.291053


# V1.3 conservative speaker policy.
#
# These are NOT new learned thresholds.
# They reuse the existing frozen V1.2
# classification bands.
#
# The policy remains observe-only.
POLICY_VERSION = "V1.3"
POLICY_MODE = "CONSERVATIVE_OBSERVE_ONLY"

POLICY_MIN_SECONDS = 1.00

POLICY_TRUSTED_AARON_THRESHOLD = (
    STRONG_AARON_THRESHOLD
)

POLICY_STRONG_BACKGROUND_THRESHOLD = (
    BACKGROUND_THRESHOLD
)


def policy_classify(
    duration_seconds: float,
    window_short_score: float,
) -> str:
    if (
        duration_seconds
        < POLICY_MIN_SECONDS
    ):
        return "SHORT_UNCERTAIN"

    if (
        window_short_score
        <= POLICY_STRONG_BACKGROUND_THRESHOLD
    ):
        return "STRONG_BACKGROUND"

    if (
        window_short_score
        >= POLICY_TRUSTED_AARON_THRESHOLD
    ):
        return "TRUSTED_AARON"

    return "UNCERTAIN"


def score_pcm(
    pcm: bytes,
) -> dict[str, object]:
    started = time.perf_counter()

    waveform = pcm24k_to_waveform(
        pcm
    )

    with MODEL_LOCK:
        with torch.inference_mode():
            value = classifier.encode_batch(
                waveform
            )

    candidate = normalise_embedding(
        value
    )

    long_score = cosine(
        candidate,
        aaron_centroid,
    )

    short_score = cosine(
        candidate,
        short_aaron_centroid,
    )

    window_samples = round(
        WINDOW_SHORT_SECONDS
        * WINDOW_SHORT_MODEL_RATE
    )

    window_step_samples = round(
        WINDOW_SHORT_STEP_SECONDS
        * WINDOW_SHORT_MODEL_RATE
    )

    window_short_scores: list[float] = []

    if (
        waveform.shape[-1]
        >= window_samples
    ):
        with MODEL_LOCK:
            with torch.inference_mode():
                for start in range(
                    0,
                    waveform.shape[-1]
                    - window_samples
                    + 1,
                    window_step_samples,
                ):
                    end = (
                        start
                        + window_samples
                    )

                    chunk = waveform[
                        :,
                        start:end,
                    ]

                    window_value = (
                        classifier.encode_batch(
                            chunk
                        )
                    )

                    window_candidate = (
                        normalise_embedding(
                            window_value
                        )
                    )

                    window_short_scores.append(
                        cosine(
                            window_candidate,
                            short_aaron_centroid,
                        )
                    )

    if window_short_scores:
        window_short_score = max(
            window_short_scores
        )

        window_short_count = len(
            window_short_scores
        )

        window_short_fallback = False

    else:
        window_short_score = short_score
        window_short_count = 1
        window_short_fallback = True

    window_short_candidate_pass = (
        window_short_score
        >= WINDOW_SHORT_CANDIDATE_THRESHOLD
    )

    score = max(
        long_score,
        short_score,
    )

    duration_seconds = (
        len(pcm)
        / (
            INPUT_RATE
            * SAMPLE_WIDTH
            * CHANNELS
        )
    )

    return {
        "ok": True,
        "score": round(
            score,
            6,
        ),
        "long_score": round(
            long_score,
            6,
        ),
        "short_score": round(
            short_score,
            6,
        ),
        "profile_method":
            "BEST_LONG_SHORT",
        "window_short_score": round(
            window_short_score,
            6,
        ),
        "window_short_candidate_threshold":
            WINDOW_SHORT_CANDIDATE_THRESHOLD,
        "window_short_candidate_pass":
            window_short_candidate_pass,
        "window_short_seconds":
            WINDOW_SHORT_SECONDS,
        "window_short_step_seconds":
            WINDOW_SHORT_STEP_SECONDS,
        "window_short_count":
            window_short_count,
        "window_short_fallback":
            window_short_fallback,
        "window_short_mode":
            "PROSPECTIVE_OBSERVE_ONLY",
        "policy_version":
            POLICY_VERSION,
        "policy_mode":
            POLICY_MODE,
        "policy_decision":
            policy_classify(
                duration_seconds,
                window_short_score,
            ),
        "policy_min_seconds":
            POLICY_MIN_SECONDS,
        "policy_trusted_aaron_threshold":
            POLICY_TRUSTED_AARON_THRESHOLD,
        "policy_strong_background_threshold":
            POLICY_STRONG_BACKGROUND_THRESHOLD,
        "policy_suppress_candidate":
            (
                policy_classify(
                    duration_seconds,
                    window_short_score,
                )
                == "STRONG_BACKGROUND"
            ),
        "policy_action":
            "OBSERVE_ONLY",
        "classification": classify(
            score
        ),
        "duration_seconds": round(
            duration_seconds,
            3,
        ),
        "strong_aaron_threshold":
            STRONG_AARON_THRESHOLD,
        "background_threshold":
            BACKGROUND_THRESHOLD,
        "reference_count":
            len(reference_embeddings),
        "short_reference_count":
            len(short_reference_embeddings),
        "inference_ms": round(
            (
                time.perf_counter()
                - started
            ) * 1000,
            1,
        ),
        "gate_enabled": False,
        "action": "OBSERVE_ONLY",
    }


class Handler(
    BaseHTTPRequestHandler
):
    server_version = (
        "JarvisSpeakerVerifier/1.0"
    )

    def log_message(
        self,
        format: str,
        *args,
    ) -> None:
        print(
            "HTTP",
            self.address_string(),
            format % args,
            flush=True,
        )

    def send_json(
        self,
        status: int,
        payload: dict[str, object],
    ) -> None:
        body = json.dumps(
            payload,
            separators=(
                ",",
                ":",
            ),
        ).encode(
            "utf-8"
        )

        self.send_response(
            status
        )

        self.send_header(
            "Content-Type",
            "application/json",
        )

        self.send_header(
            "Content-Length",
            str(
                len(body)
            ),
        )

        self.end_headers()

        self.wfile.write(
            body
        )

    def do_GET(
        self,
    ) -> None:
        if self.path != "/health":
            self.send_json(
                404,
                {
                    "ok": False,
                    "error":
                        "not_found",
                },
            )
            return

        self.send_json(
            200,
            {
                "ok": True,
                "service":
                    "jarvis-speaker-verifier",
                "model":
                    MODEL_SOURCE,
                "reference_count":
                    len(
                        reference_embeddings
                    ),
                "short_reference_count":
                    len(
                        short_reference_embeddings
                    ),
                "profile_method":
                    "BEST_LONG_SHORT",
                "window_short_candidate_threshold":
                    WINDOW_SHORT_CANDIDATE_THRESHOLD,
                "window_short_seconds":
                    WINDOW_SHORT_SECONDS,
                "window_short_step_seconds":
                    WINDOW_SHORT_STEP_SECONDS,
                "window_short_mode":
                    "PROSPECTIVE_OBSERVE_ONLY",
                "policy_version":
                    POLICY_VERSION,
                "policy_mode":
                    POLICY_MODE,
                "policy_min_seconds":
                    POLICY_MIN_SECONDS,
                "policy_trusted_aaron_threshold":
                    POLICY_TRUSTED_AARON_THRESHOLD,
                "policy_strong_background_threshold":
                    POLICY_STRONG_BACKGROUND_THRESHOLD,
                "policy_action":
                    "OBSERVE_ONLY",
                "strong_aaron_threshold":
                    STRONG_AARON_THRESHOLD,
                "background_threshold":
                    BACKGROUND_THRESHOLD,
                "gate_enabled":
                    False,
                "mode":
                    "observe_only",
            },
        )

    def do_POST(
        self,
    ) -> None:
        if self.path != "/score":
            self.send_json(
                404,
                {
                    "ok": False,
                    "error":
                        "not_found",
                },
            )
            return

        raw_length = self.headers.get(
            "Content-Length"
        )

        try:
            content_length = int(
                raw_length or "0"
            )
        except ValueError:
            content_length = 0

        if (
            content_length <= 0
            or content_length
            > MAX_PCM_BYTES
        ):
            self.send_json(
                400,
                {
                    "ok": False,
                    "error":
                        "invalid_content_length",
                },
            )
            return

        pcm = self.rfile.read(
            content_length
        )

        try:
            result = score_pcm(
                pcm
            )

        except Exception as exc:
            self.send_json(
                500,
                {
                    "ok": False,
                    "error":
                        type(exc).__name__,
                    "detail":
                        str(exc)[:300],
                },
            )
            return

        self.send_json(
            200,
            result,
        )


server = ThreadingHTTPServer(
    (
        HOST,
        PORT,
    ),
    Handler,
)

print(
    "PASS: listening",
    f"http://{HOST}:{PORT}",
    flush=True,
)

server.serve_forever()
