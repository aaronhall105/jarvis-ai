# Validation and quality gates

This file describes the **current repository validation model**. Historical
release-specific test reports are preserved under `docs/archive/test-reports/`.

## Default-branch identity

At this documentation refresh the `conversation-engine` branch identifies
itself as:

- Jarvis `19.0.0-alpha17`;
- Core application API `3.7.0`;
- realtime protocol `2`.

## Continuous integration

`.github/workflows/jarvis-ci.yml` currently defines the main quality gates.

### Core and integration

- install Core and improvement-worker dependencies;
- compile `bridge/app`;
- run the Core pytest suite;
- run Home Assistant streaming/closure integration tests;
- validate the Home Assistant release package.

### Correctness and security

- Ruff correctness checks for critical Python errors;
- Bandit high-severity scan of Core application code;
- Python dependency audit;
- pull-request dependency review.

### Android

- Java 17;
- Android SDK 36;
- Gradle 9.4.1;
- offline wake asset preparation;
- Android debug unit tests;
- debug APK assembly.

CodeQL and Android OTA workflows are maintained separately.

## Release claims

A passing CI run shows that the checked commit passed the configured automated
gates. It does not by itself prove every deployment-specific path, external
service or physical device has been live-tested.

Open PR validation belongs to that PR until the change is merged.

The Alpha19 production-hardening PR currently reports broader validation, but it
remains draft work and must not be presented as default-branch certification.

## Historical report

The old root `TESTED.md` described only the Self-Improvement v14 package. It is
preserved at:

[`docs/archive/test-reports/TESTED_SELF_IMPROVEMENT_V14.md`](docs/archive/test-reports/TESTED_SELF_IMPROVEMENT_V14.md)
