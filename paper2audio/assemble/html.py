from __future__ import annotations

import base64
import json
from pathlib import Path

from ..parse.models import DocumentModel
from ..script.models import (
    EquationSegment,
    ExplainSegment,
    FigureCueSegment,
    PodcastIntroSegment,
    PodcastOutroSegment,
    ReadSegment,
    TimedSegment,
)


def _fmt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _img_b64(path: str | None) -> str | None:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    suffix = p.suffix.lower()
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".pdf": "application/pdf"}.get(suffix, "image/png")
    if mime == "application/pdf":
        return None
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"


def build_companion_html(
    timed_segments: list[TimedSegment],
    doc: DocumentModel,
    output_path: Path,
):
    fig_map = {f.ref: f for f in doc.figures}

    rows: list[str] = []
    for ts in timed_segments:
        seg = ts.segment
        t = _fmt_time(ts.start_seconds)
        t_tag = f'<span class="ts">{t}</span>'

        if isinstance(seg, ReadSegment):
            text = " ".join(doc.chunks[cid].text for cid in seg.chunk_ids if cid in doc.chunks)
            rows.append(f'<div class="seg read">{t_tag}<p>{text}</p></div>')

        elif isinstance(seg, ExplainSegment):
            rows.append(f'<div class="seg explain">{t_tag}<p>💬 {seg.text}</p></div>')

        elif isinstance(seg, FigureCueSegment):
            fig = fig_map.get(seg.ref)
            img_html = ""
            if fig:
                b64 = _img_b64(fig.image_path)
                if b64:
                    img_html = f'<img src="{b64}" alt="{fig.label}" style="max-width:100%;margin-top:8px">'
            rows.append(
                f'<div class="seg figure">{t_tag}'
                f'<p>📊 {seg.text}</p>{img_html}</div>'
            )

        elif isinstance(seg, EquationSegment):
            rows.append(f'<div class="seg equation">{t_tag}<p>∑ {seg.text}</p></div>')

        elif isinstance(seg, PodcastIntroSegment):
            rows.append(f'<div class="seg intro">{t_tag}<p>🎙️ {seg.text}</p></div>')

        elif isinstance(seg, PodcastOutroSegment):
            rows.append(f'<div class="seg outro">{t_tag}<p>🎙️ {seg.text}</p></div>')

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{doc.title} — paper2audio companion</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 760px; margin: 40px auto; padding: 0 20px; color: #1a1a1a; line-height: 1.6; }}
  h1 {{ font-size: 1.3rem; margin-bottom: 4px; }}
  .meta {{ color: #666; font-size: 0.9rem; margin-bottom: 32px; }}
  .seg {{ margin: 8px 0; padding: 10px 14px; border-radius: 6px; border-left: 3px solid transparent; }}
  .read {{ background: #f9f9f9; border-color: #ddd; }}
  .explain {{ background: #fffbea; border-color: #f0c040; }}
  .figure {{ background: #f0f7ff; border-color: #4a90d9; }}
  .equation {{ background: #f5f0ff; border-color: #9b59b6; }}
  .intro, .outro {{ background: #f0fff4; border-color: #2ecc71; font-style: italic; }}
  .ts {{ font-size: 0.75rem; color: #999; font-variant-numeric: tabular-nums; display: block; margin-bottom: 4px; }}
  p {{ margin: 0; }}
</style>
</head>
<body>
<h1>{doc.title}</h1>
<p class="meta">{", ".join(doc.authors)}</p>
{"".join(rows)}
</body>
</html>"""

    output_path.write_text(html, encoding="utf-8")
