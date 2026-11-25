# paper2audio

Turns research papers into listenable audiobooks.

One voice reads the paper verbatim. A second voice acts as a teacher — explaining terms when they first appear, narrating figures, translating equations, and setting context before dense sections. Output is a chaptered `.m4b` you can throw on your phone, plus an RSS feed that pushes every processed paper as a podcast episode to any podcast app.

---

## How it works

```
arXiv ID or PDF
      ↓
   ingest → parse → clean → context pass (LLM) → script generation (LLM/section)
                                                          ↓
                                              validate: every chunk read exactly once
                                                          ↓
                                             TTS synthesis (two voices, cached)
                                                          ↓
                                        ffmpeg assembly → m4b + mp3 + companion HTML
                                                          ↓
                                                   RSS feed updated
```

---

## Design Docs

- [System Architecture](docs/01-system-architecture.md) — component diagram, modules, directory structure
- [Pipeline Design](docs/02-pipeline.md) — data flow, sequence diagram, stage details, caching, validation
- [Data Models](docs/03-data-models.md) — DocumentModel, segment types, job model, config schema
- [API Design](docs/04-api-design.md) — endpoints, RSS feed, CLI interface, web UI flow
- [Voice Design](docs/05-voice-design.md) — the teacher model, five trigger points, LLM prompts, validation
- [Deployment](docs/06-deployment.md) — Fly.io setup, Dockerfile, cost estimate

---

## Setup

_Coming soon — see [deployment doc](docs/06-deployment.md) for the full plan._

```bash
# Install deps
uv sync

# Copy env template
cp .env.example .env
# Add ANTHROPIC_API_KEY and OPENAI_API_KEY to .env

# CLI
paper2audio 1706.03762 --level normal

# Web server
uvicorn paper2audio.web.app:app --reload
```

---

## RSS Podcast Feed

After deploying, subscribe to your feed once in any podcast app:

```
https://<your-app>.fly.dev/feed.rss
```

Every paper you process appears as a new episode and downloads automatically. Works with Apple Podcasts, Overcast, Pocket Casts, Spotify — no subscription required.

---

## Stack

- Python 3.11+, uv, Pydantic v2, FastAPI
- Claude API (Anthropic) for context pass + script generation
- OpenAI TTS for audio synthesis
- ffmpeg for assembly, chapter markers, loudness normalization
- Fly.io for hosting
