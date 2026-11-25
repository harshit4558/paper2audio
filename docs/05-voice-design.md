# Voice Design — The Teacher Model

## Concept

The experience should feel like a knowledgeable professor reading a paper aloud to a class. They don't just read robotically — they set context before hard sections, warn you before a dense equation, say "look at Figure 3 now", and occasionally recap after a lot of information lands at once.

There are two distinct voices:
- **Voice A — The Reader**: Neutral, clear narrator. Reads the paper's exact text verbatim. Never paraphrases.
- **Voice B — The Teacher**: Warmer, more conversational. Explains, cues, narrates. Never repeats what was just read.

---

## Five Trigger Points for Voice B

### 1. Section Transition
Before every top-level section, Voice B briefly sets the scene.

> *"Alright, we're moving into the experiments section. This is where they put the architecture we just discussed to the test on translation benchmarks."*

Short — one sentence is enough. Gives your brain a moment to shift gears. Skipped for `light` level.

### 2. Term Introduction
The first time a key term appears (from the glossary built in the context pass), Voice B steps in right after the sentence that introduces it. Not a dictionary definition — a one-liner that gives the intuition.

> *"So 'positional encoding' here just means they're baking position information directly into the input embeddings — the model has no other way to know which word came first."*

A term is explained only once. If it appears again later, Voice B stays silent.

### 3. Dense Equation
Before a significant equation, Voice B primes the listener with what to expect:

> *"This next formula is the core of the paper. It looks dense but the idea is simple: it's a weighted average, where the weights come from how relevant each word is to the current one."*

After the equation text (read by Voice A as spoken math), Voice B optionally adds the key takeaway for `normal`/`heavy` levels:

> *"The main thing to take away here is the softmax — it turns raw similarity scores into probabilities that sum to one."*

### 4. Figure Cue
Whenever a figure is referenced, Voice B interrupts to tell you to look and what you'll see. This works even if you're not looking.

> *"Pause here and take a look at Figure 3. It shows the attention weight distribution across a sentence — you can see the model has learned that 'it' attends strongly to 'animal', which is exactly the kind of coreference a human reader would notice."*

Always includes: (a) which figure, (b) what it's showing, (c) what's notable about it. Source: the `figure_descriptions` from the context pass.

### 5. Post-Dense-Paragraph Recap
After a paragraph that introduced 3+ new concepts, Voice B adds a brief landing sentence on `heavy` level only:

> *"So those three components — queries, keys, and values — are the building blocks for every attention operation in this paper."*

---

## LLM Prompt Structure

### Context Pass Prompt
One call, full paper. Returns the ContextCache used by all script generation calls.

```
You are reading a research paper to understand it deeply before narrating it.

Paper: <full text>
User profile: <profile.md contents>

Return JSON:
{
  "glossary": {"term": "plain-language definition (1 sentence)"},
  "main_contributions": ["...", "..."],
  "figure_descriptions": {"fig1": "2-sentence description of what this figure shows and what's notable about it"},
  "paper_difficulty": "introductory | standard | dense"
}

For the glossary: include every term a reader with this profile might find unclear. Skip terms the profile says they know well.
For figure_descriptions: describe what you can infer from the caption and context — be specific about what the reader should look for.
```

### Script Generation Prompt
One call per section.

```
You are producing the narration script for a research paper audiobook.

CONTEXT:
<ContextCache JSON>

USER PROFILE:
<profile.md>

EXPLANATION LEVEL: normal
(light = only genuinely opaque terms | normal = section intros + key terms + figure cues + equation priming | heavy = all of the above + post-dense-paragraph recaps)

ALREADY EXPLAINED TERMS: [positional encoding, attention, ...]
(Do not explain these again.)

SECTION: <section title>
CHUNKS:
[41] "We propose a new simple network architecture..."
[42] "The Transformer, based solely on attention mechanisms..."
...

RULES:
1. Return a JSON array of segments in order.
2. ReadSegment: {"type": "read", "chunk_ids": [41, 42]} — use consecutive chunk IDs. Never write the paper's text. Every chunk must appear in exactly one read segment.
3. ExplainSegment: {"type": "explain", "text": "..."} — Voice B, 1–3 sentences max. Never restate what was just read. Never explain terms already in ALREADY EXPLAINED TERMS.
4. FigureCueSegment: {"type": "figure_cue", "ref": "fig1", "text": "..."} — place immediately when the chunk referencing this figure is read.
5. EquationSegment: {"type": "equation", "ref": "eq1", "text": "..."} — spoken math or plain-language description for complex equations.
6. The explainer voice is a knowledgeable friend — direct, specific, informal but precise. Not a textbook.
7. Do not add explain segments for concepts the user profile says they know well.

Return only the JSON array. No other text.
```

---

## Validation Rules

After each section's script is generated, the validator enforces:

```python
def validate_section_script(
    segments: list[Segment],
    section_chunk_ids: list[int]
) -> ValidationResult:
    read_ids = []
    for seg in segments:
        if seg.type == "read":
            read_ids.extend(seg.chunk_ids)

    covered = set(read_ids)
    expected = set(section_chunk_ids)

    errors = []
    if covered != expected:
        missing = expected - covered
        extra = covered - expected
        if missing:
            errors.append(f"Missing chunks: {sorted(missing)}")
        if extra:
            errors.append(f"Unknown chunk IDs: {sorted(extra)}")
    if len(read_ids) != len(set(read_ids)):
        duplicates = [id for id in read_ids if read_ids.count(id) > 1]
        errors.append(f"Duplicate chunk IDs: {set(duplicates)}")

    return ValidationResult(valid=len(errors) == 0, errors=errors)
```

If invalid, the validation errors are appended to the prompt and the section is retried. Example retry message:

```
The previous attempt was invalid:
- Missing chunks: [44, 45]
- Duplicate chunk IDs: {42}

Fix these issues and return a corrected segment list.
```

---

## Explanation Level Comparison

| Trigger | `light` | `normal` | `heavy` |
|---------|---------|---------|---------|
| Section transition | — | ✓ | ✓ |
| Term introduction | Only truly opaque | Key terms from glossary | All glossary terms |
| Equation priming | — | ✓ (before) | ✓ (before + after) |
| Figure cue | ✓ | ✓ | ✓ |
| Post-dense-paragraph recap | — | — | ✓ |

---

## Voice Tone Guidelines (in prompt)

**Voice B writes like this:**
> "So what they're saying here is that instead of processing words one at a time, they look at the whole sequence at once — that's the key insight."

**Voice B does NOT write like this:**
> "The aforementioned methodology utilizes a novel attention-based mechanism which, as stated in the previous paragraph, processes sequences in parallel."

One test: if you could say it in a conversation with a colleague over coffee, it's right. If it sounds like it belongs in a textbook, rewrite it.
