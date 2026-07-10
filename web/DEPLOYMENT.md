# Mate Voice web deployment

This app is based on LiveKit's official `agent-starter-react` frontend. It
discovers the persistent instance room from `hermes-livekit` and displays agent
state, audio, camera/screen media, text chat, and live transcriptions.

## Local development

The repository's `.env.local` uses `mate-token.drascom.uk` for room discovery,
so the room is not duplicated in frontend configuration.

```bash
pnpm dev
```

Open `http://localhost:3000`.

## VPS deployment

```bash
cp .env.production.example .env.production
# Preferred: fill MATE_TOKEN_ENDPOINT and MATE_VOICE_CLIENT_KEY.
# The direct-mint fallback uses MATE_DISCOVERY_ENDPOINT + LIVEKIT_*.
docker compose --env-file .env.production up -d --build
```

The container binds to `127.0.0.1:3000` by default. Put Caddy or Nginx in front:

```caddyfile
mate.example.com {
    reverse_proxy 127.0.0.1:3000
}
```

Use a trusted HTTPS certificate. Browsers do not grant microphone/camera access
to a remote plain-HTTP origin. The LiveKit endpoint also needs a public WSS URL,
the required WebRTC UDP/TCP ports, and TURN for reliable connectivity behind
NAT and restrictive networks.

`WEB_USERNAME` and `WEB_PASSWORD` protect both the UI and token endpoint with
HTTP Basic authentication. Never expose `MATE_VOICE_CLIENT_KEY` or
`LIVEKIT_API_SECRET` in a `NEXT_PUBLIC_*` variable or commit production
environment files. Clients receive the resolved room from Hermes; do not add a
fixed `MATE_LIVEKIT_ROOM` unless intentionally overriding discovery.

## Advanced voice behavior

Browser-side echo cancellation, noise suppression, and automatic gain control
are enabled. The web client continuously publishes microphone audio and renders
Hermes' `lk.agent.state` plus `lk.transcription` messages.

VAD, Smart Turn v3 end-of-utterance detection, silence thresholds, speaker-gated
barge-in, STT, and cancellable TTS remain in `hermes-livekit`. Keeping these on
the server ensures identical behavior for Mac, iOS, and web clients.
