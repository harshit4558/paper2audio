# Pipeline Design

## Full Data Flow

```mermaid
flowchart TD
    INPUT([arXiv ID or PDF Upload]) --> INGEST

    subgraph INGEST["Stage 1: Ingest"]
        I1{arXiv or PDF?}
        I2[Download .tar.gz from arXiv e-print]
        I3[Accept PDF bytes from upload]
        I1 -->|arXiv| I2
        I1 -->|PDF| I3
    end

    INGEST --> PARSE

    subgraph PARSE["Stage 2: Parse"]
        P1{Source type?}
        P2[Extract .tex files + figures]
        P3[marker-pdf → Markdown]
        P4[LaTeX parser]
        P5[Markdown parser]
        P6[DocumentModel]
        P1 -->|LaTeX| P2 --> P4 --> P6
        P1 -->|PDF| P3 --> P5 --> P6
    end

    PARSE --> CLEAN

    subgraph CLEAN["Stage 3: Clean"]
        C1[Strip citation markers]
        C2[Resolve ref and cite to spoken text]
        C3[Footnotes → short asides]
        C4[Drop URLs, expand abbreviations]
        C5[Exclude references section]
        C6[Cleaned DocumentModel]
        C1 --> C2 --> C3 --> C4 --> C5 --> C6
    end

    CLEAN --> CONTEXT

    subgraph CONTEXT["Stage 4: Context Pass"]
        CT1[Single LLM call — full paper]
        CT2[ContextCache: glossary + contributions + figure descriptions]
        CT1 --> CT2
    end

    CONTEXT --> SCRIPT

    subgraph SCRIPT["Stage 5: Script Generation"]
        S1[For each section: LLM call with section text + context + profile + level]
        S2[JSON segment list returned]
        S3{Chunk coverage valid?}
        S4[Retry — up to 3x]
        S5[Final ordered segment list]
        S1 --> S2 --> S3
        S3 -->|No| S4 --> S1
        S3 -->|Yes| S5
    end

    SCRIPT --> TTS

    subgraph TTS["Stage 6: TTS"]
        T1[For each segment: check cache]
        T2{Cached?}
        T3[Call OpenAI TTS with voice assignment]
        T4[Save .wav to cache]
        T5[Collect all segment audio paths]
        T1 --> T2
        T2 -->|Yes| T5
        T2 -->|No| T3 --> T4 --> T5
    end

    TTS --> ASSEMBLE

    subgraph ASSEMBLE["Stage 7: Assembly"]
        A1[Stitch segments with pauses]
        A2[Add chimes before figure cues]
        A3[Add long pauses between sections]
        A4[Normalize loudness — EBU R128]
        A5[Write chapter markers per section]
        A6[Encode paper.m4b and paper.mp3]
        A7[Build companion.html]
        A8[Write script.json with timestamps]
        A9[Update RSS feed]
        A1 --> A2 --> A3 --> A4 --> A5 --> A6
        A6 --> A7
        A6 --> A8
        A6 --> A9
    end

    ASSEMBLE --> OUTPUT([ZIP: m4b + mp3 + companion.html + script.json])
    A9 --> RSS([RSS feed updated])
```

---

## Processing Sequence

```mermaid
sequenceDiagram
    participant U as User
    participant API as FastAPI
    participant W as Worker
    participant AX as arXiv
    participant LLM as Claude API
    participant TTS as OpenAI TTS
    participant FF as ffmpeg

    U->>API: POST /jobs {arxiv_id, level, include_appendix}
    API-->>U: {job_id, status: "queued"}
    API->>W: Enqueue job

    Note over W: Stage 1 — Ingest
    W->>AX: GET /e-print/{id}
    AX-->>W: .tar.gz source

    Note over W: Stage 2 — Parse
    W->>W: Extract LaTeX + figures → DocumentModel

    Note over W: Stage 3 — Clean
    W->>W: Strip citations, resolve refs, normalize

    Note over W: Stage 4 — Context Pass
    W->>LLM: Full paper text
    LLM-->>W: {glossary, contributions, figure_descriptions}
    W->>W: Save ContextCache to disk

    Note over W: Stage 5 — Script Generation
    loop per section
        W->>LLM: Section text + context + profile + level + explained_terms
        LLM-->>W: JSON segment list
        W->>W: Validate chunk coverage
        alt Invalid
            W->>LLM: Retry with validation error feedback
        end
    end

    Note over W: Stage 6 — TTS
    loop per segment (skips cached)
        W->>TTS: {text, voice}
        TTS-->>W: audio bytes
        W->>W: Save to cache/{hash}.wav
    end

    Note over W: Stage 7 — Assemble
    W->>FF: Stitch + chapters + normalize
    FF-->>W: paper.m4b, paper.mp3
    W->>W: Build companion.html, script.json
    W->>W: Update RSS feed XML

    W-->>API: Job complete, output paths
    U->>API: GET /jobs/{id} (SSE progress stream)
    API-->>U: data: {stage: "done", download_url: "..."}
```

---

## Stage Details

### Stage 1 — Ingest

**Input:** arXiv ID string (e.g. `1706.03762`) or PDF bytes  
**Output:** Raw source bytes on disk (`cache/raw/<id>/`)  
**Cache key:** arXiv ID or SHA256 of PDF bytes

arXiv LaTeX source is downloaded as a `.tar.gz` from `https://arxiv.org/e-print/<id>`. The tarball is extracted and the main `.tex` file is identified by scanning for `\documentclass`. Figures are extracted as image files and indexed by their filename.

PDF fallback uses `marker-pdf` to convert to structured Markdown, which the PDF parser then processes into a DocumentModel.

---

### Stage 2 — Parse

**Input:** Extracted LaTeX files or Markdown (from marker-pdf)  
**Output:** `DocumentModel` serialized to `cache/parsed/<id>.json`  
**Cache key:** Hash of raw source content

The LaTeX parser walks the AST to extract:
- Sections and their hierarchy (`\section`, `\subsection`, `\subsubsection`)
- Text split into sentence-level chunks (globally numbered from 0)
- Figure environments (`\begin{figure}`) with label, caption, image path
- Equation environments with label and LaTeX source
- Footnotes as separate text nodes

The references section (`\section*{References}`, `\bibliography{...}`) is excluded at parse time.

Both parsers must produce identical DocumentModel structure. The PDF path produces lower-fidelity output (no raw LaTeX for equations) but is structurally compatible.

---

### Stage 3 — Clean

**Input:** `DocumentModel`  
**Output:** `DocumentModel` with cleaned text fields (`cache/cleaned/<id>.json`)  
**Cache key:** Hash of parsed model

Transformations applied to every chunk's text:

| Transform | Rule |
|-----------|------|
| Citation removal | Strip `[12]`, `(Smith et al., 2021)`, `\cite{...}` |
| Ref resolution | `\ref{fig:arch}` → "Figure 3", `\ref{sec:method}` → "Section 4" |
| URL removal | Drop `http://...` and `https://...` links |
| Abbreviation expansion | `e.g.` → "for example", `i.e.` → "that is", `et al.` → "and colleagues" |
| Footnote handling | Move footnote text to a separate `explain` chunk appended after the paragraph |
| Equation placeholder | Inline `$...$` → "the expression" or spoken form for simple cases |
| Hyphen normalization | Em dashes and double-hyphens → ", " for natural speech rhythm |

---

### Stage 4 — Context Pass

**Input:** Full cleaned DocumentModel  
**Output:** `ContextCache` JSON (`cache/context/<id>.json`)  
**Cache key:** Hash of cleaned model

A single LLM call with the entire paper (title, abstract, all section text). The prompt asks for:

```json
{
  "glossary": {
    "term": "one-sentence plain-language definition"
  },
  "main_contributions": ["...", "..."],
  "figure_descriptions": {
    "fig1": "what this figure is actually showing, in 2 sentences"
  },
  "paper_difficulty": "introductory | standard | dense"
}
```

This output is passed to every Script Generation call. It ensures the explainer voice describes figures accurately and explains terms consistently across sections.

---

### Stage 5 — Script Generation

**Input:** Cleaned section text + ContextCache + user profile + explanation level + set of already-explained terms  
**Output:** Ordered `List[Segment]` per section, validated (`cache/script/<id>/<section_id>.json`)  
**Cache key:** Hash of section content + context + level

One LLM call per section. The model receives:
- The section's chunks with their IDs
- The ContextCache
- The user's `profile.md`
- The explanation level (`light` / `normal` / `heavy`)
- A set of terms already explained in earlier sections (to avoid repetition)

The model returns a flat ordered list of segments. The validator (see [Validation](#validation--chunk-coverage)) checks correctness before accepting the result. Up to 3 retries with the validation error message appended to the prompt.

The explainer voice is instructed to write as a knowledgeable teacher: direct, specific, never restating the paper's words.

---

### Stage 6 — TTS

**Input:** Ordered segment list  
**Output:** Per-segment `.wav` files  
**Cache key:** SHA256 of `text + voice_id`

Voice assignment:
- `read` segments → Voice A (neutral, clear narrator)
- `explain`, `figure_cue`, `equation` segments → Voice B (warmer, conversational)

Each segment is synthesized independently and cached. Re-runs only synthesize segments not already on disk. The TTS provider is behind an abstract interface so OpenAI TTS, ElevenLabs, or Kokoro can be swapped via `config.yaml`.

---

### Stage 7 — Assembly

**Input:** Ordered segment list with audio paths  
**Output:** `paper.m4b`, `paper.mp3`, `companion.html`, `script.json`

Assembly steps in order:

1. Concatenate segment audio files with ffmpeg, inserting:
   - 300ms pause between consecutive `read` segments
   - 600ms pause before/after `explain` segments
   - Soft chime (included asset) + 800ms pause before `figure_cue` segments
   - 1500ms pause between sections
2. Normalize loudness to EBU R128 (`-23 LUFS`)
3. Write chapter markers (one per top-level section) into the m4b container
4. Encode to `.m4b` (AAC, 64kbps) and `.mp3` (128kbps)
5. Build `companion.html` with timestamps, figures, and styled script (see [API Design](./04-api-design.md))
6. Write `script.json` with per-segment start/end timestamps
7. Append the paper to `outputs/feed.xml` (RSS feed)

---

## Caching Strategy

| Stage | Cache location | Cache key |
|-------|---------------|-----------|
| Ingest | `cache/raw/<id>/` | arXiv ID or PDF hash |
| Parse | `cache/parsed/<id>.json` | Hash of raw source |
| Clean | `cache/cleaned/<id>.json` | Hash of parsed model |
| Context | `cache/context/<id>.json` | Hash of cleaned model |
| Script | `cache/script/<id>/<section>.json` | Hash of section + context + level |
| TTS | `cache/audio/<hash>.wav` | SHA256 of text + voice |
| Final output | `outputs/<id>/` | — |

---

## Validation — Chunk Coverage

After script generation for each section, the following invariants are enforced:

1. **Complete coverage:** every chunk ID in the section appears in at least one `read` segment
2. **No duplication:** no chunk ID appears more than once across all `read` segments
3. **Ordering:** chunk IDs within a `read` segment are consecutive and appear in document order
4. **No out-of-section references:** chunk IDs belong to the current section only

If any invariant fails, the validator formats a structured error message and the section is re-generated. After 3 failed attempts the job halts with a detailed error report naming the section and the specific failure.

This is the hardest guarantee in the system — no chunk of the paper is ever skipped or read twice.
