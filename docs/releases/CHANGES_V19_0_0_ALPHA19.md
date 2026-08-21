# Jarvis 19.0.0-alpha19

- Retains Alpha18 generation-safe cancellation and recovery.
- Improves normal-volume open-speaker barge-in by reducing assistant output gain
  only while full-duplex Android recognition is armed.
- Keeps full output gain on headphones, Bluetooth, and other private routes.
- Adds deterministic acoustic-policy coverage and runtime gain diagnostics.

## Core reliability and intelligence

- Adds generation-safe interruption across OpenAI generation, ElevenLabs
  streaming, local turn tasks, and Voice Preview playback.
- Grounds room-scoped commands in the active Voice Preview area and improves
  conservative speech correction for real Home Assistant entities.
- Adds trustworthy Memory v4 metadata: revision history, provenance,
  confidence, confirmation time, expiry, recoverable retirement, and restore.
- Adds explainable proactive decisions with confidence evidence, quiet hours,
  an attention budget, user suppression feedback, and approval-only learning
  proposals.
- Routes proactive speech through Home Assistant's native Assist Satellite
  conversation service so an announcement can accept a short spoken reply.
- Adds camera-event evidence, presence context, room routing, and conservative
  low-confidence suppression without visual identity claims.
- Adds production diagnostics for voice reliability, recognition repairs,
  stale events, interruption latency, and memory integrity.

## Quality and operations

- Extends CI to cover backend tools and a daily scheduled verification run.
- Adds deterministic regression coverage for cancellation races, stale audio,
  delayed completion events, repeated interruptions, memory migrations,
  camera initiatives, room routing, and speech corrections.
- Ignores downloaded Android wake assets and local upgrade workspaces so the
  repository contains source and reproducible build instructions rather than
  generated binaries or secret-bearing migration bundles.
