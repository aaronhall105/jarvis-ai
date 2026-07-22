# Jarvis Core Architecture

## Purpose

Jarvis Core is a low-latency conversational bridge connecting:

1. Home Assistant Voice Preview Edition microphones
2. OpenAI Realtime speech-to-speech sessions
3. Restricted Home Assistant tools
4. Room-specific audio outputs

## Core principles

- Realtime audio first
- Explicitly permitted Home Assistant actions
- No arbitrary service-call access for the AI
- Secrets stored outside Git
- Modular components
- Room-aware microphone and speaker routing
- Recoverable Voice Preview firmware

## Planned components

- Core API and dashboard
- OpenAI Realtime client
- Home Assistant WebSocket client
- Tool permission layer
- Audio session manager
- Voice Preview network audio receiver
- Speaker router
- Conversation memory
- Frigate and vision adapters

## Development stages

1. Core service and health monitoring
2. OpenAI Realtime connection
3. Home Assistant connection
4. Restricted tool execution
5. Test microphone and speaker
6. Voice Preview custom audio firmware
7. Ceiling-speaker streaming
8. Room awareness and memory
