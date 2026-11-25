from __future__ import annotations

import re
from pathlib import Path

from pylatexenc.latexwalker import (
    LatexEnvironmentNode,
    LatexMacroNode,
    LatexWalker,
    LatexGroupNode,
    LatexCharsNode,
)

from .models import Chunk, ChunkType, DocumentModel, Equation, Figure, Section


# -- helpers ------------------------------------------------------------------

def _node_text(node) -> str:
    if node is None:
        return ""
    if isinstance(node, LatexCharsNode):
        return node.chars
    if isinstance(node, LatexGroupNode):
        return "".join(_node_text(n) for n in (node.nodelist or []))
    if hasattr(node, "nodelist") and node.nodelist:
        return "".join(_node_text(n) for n in node.nodelist)
    return getattr(node, "chars", "")


def _arg_text(macro_node, arg_index: int = 0) -> str:
    try:
        return _node_text(macro_node.nodeargd.argnlist[arg_index]).strip()
    except (AttributeError, IndexError, TypeError):
        return ""


def _split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
    return [p.strip() for p in parts if p.strip()]


_REFS_PATTERN = re.compile(
    r"\\section\*?\{references\}|\\bibliography\{|\\begin\{references\}",
    re.IGNORECASE,
)


def _split_at_references(tex: str) -> str:
    match = _REFS_PATTERN.search(tex)
    if match:
        return tex[: match.start()]
    return tex


# -- main parser --------------------------------------------------------------

class LatexParser:
    def __init__(self):
        self._chunk_counter = 0
        self._sections: list[Section] = []
        self._chunks: dict[int, Chunk] = {}
        self._figures: list[Figure] = []
        self._equations: list[Equation] = []
        self._labels: dict[str, str] = {}  # label key → human name
        self._fig_counter = 0
        self._eq_counter = 0
        self._current_section_id: str = "abstract"

    def _next_chunk_id(self) -> int:
        cid = self._chunk_counter
        self._chunk_counter += 1
        return cid

    def _add_chunk(self, text: str, chunk_type: ChunkType = ChunkType.BODY) -> int:
        cid = self._next_chunk_id()
        self._chunks[cid] = Chunk(
            id=cid, text=text, section_id=self._current_section_id, chunk_type=chunk_type
        )
        section = next((s for s in self._sections if s.id == self._current_section_id), None)
        if section:
            section.chunk_ids.append(cid)
        return cid

    def _add_text_as_chunks(self, text: str, chunk_type: ChunkType = ChunkType.BODY):
        for sentence in _split_sentences(text):
            if sentence:
                self._add_chunk(sentence, chunk_type)

    def parse(self, tex_source: str, paper_id: str, source_dir: Path) -> DocumentModel:
        tex_source = _split_at_references(tex_source)

        title = self._extract_title(tex_source)
        authors = self._extract_authors(tex_source)
        abstract = self._extract_abstract(tex_source)

        # abstract section
        abs_section = Section(id="abstract", title="Abstract", level=1)
        self._sections.append(abs_section)
        self._current_section_id = "abstract"
        self._add_text_as_chunks(abstract)

        # collect label→number mappings before full parse
        self._collect_labels(tex_source)

        # walk document body
        body = self._extract_body(tex_source)
        self._walk_tex(body, source_dir)

        return DocumentModel(
            paper_id=paper_id,
            title=title,
            authors=authors,
            abstract=abstract,
            sections=self._sections,
            chunks=self._chunks,
            figures=self._figures,
            equations=self._equations,
        )

    def _extract_title(self, tex: str) -> str:
        m = re.search(r"\\title\{([^}]+)\}", tex, re.DOTALL)
        if m:
            return re.sub(r"\\[a-zA-Z]+\{?", "", m.group(1)).replace("}", "").strip()
        return "Untitled"

    def _extract_authors(self, tex: str) -> list[str]:
        m = re.search(r"\\author\{([^}]+)\}", tex, re.DOTALL)
        if not m:
            return []
        raw = m.group(1)
        raw = re.sub(r"\\inst\{[^}]*\}", "", raw)
        raw = re.sub(r"\\thanks\{[^}]*\}", "", raw)
        parts = re.split(r"\\and|,", raw)
        return [re.sub(r"\s+", " ", p).strip() for p in parts if p.strip()]

    def _extract_abstract(self, tex: str) -> str:
        m = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", tex, re.DOTALL)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip()
        return ""

    def _extract_body(self, tex: str) -> str:
        m = re.search(r"\\begin\{document\}(.*?)\\end\{document\}", tex, re.DOTALL)
        if m:
            return m.group(1)
        return tex

    def _collect_labels(self, tex: str):
        fig_counter = 0
        eq_counter = 0
        sec_counter: list[int] = [0, 0, 0]

        for m in re.finditer(
            r"\\(section|subsection|subsubsection|begin\{figure|begin\{equation|begin\{align)\b.*?\\label\{([^}]+)\}",
            tex,
            re.DOTALL,
        ):
            kind = m.group(1)
            label = m.group(2)
            if "figure" in kind:
                fig_counter += 1
                self._labels[label] = f"Figure {fig_counter}"
            elif "equation" in kind or "align" in kind:
                eq_counter += 1
                self._labels[label] = f"Equation {eq_counter}"
            elif kind == "section":
                sec_counter[0] += 1
                sec_counter[1] = 0
                sec_counter[2] = 0
                self._labels[label] = f"Section {sec_counter[0]}"
            elif kind == "subsection":
                sec_counter[1] += 1
                self._labels[label] = f"Section {sec_counter[0]}.{sec_counter[1]}"

    def _resolve_ref(self, label: str) -> str:
        return self._labels.get(label, label)

    def _walk_tex(self, tex: str, source_dir: Path):
        section_re = re.compile(
            r"\\(section|subsection|subsubsection)\*?\{([^}]+)\}(?:\s*\\label\{([^}]*)\})?",
            re.DOTALL,
        )
        figure_re = re.compile(
            r"\\begin\{figure\*?\}(.*?)\\end\{figure\*?\}", re.DOTALL
        )
        equation_re = re.compile(
            r"\\begin\{(equation|align|gather)\*?\}(.*?)\\end\{\1\*?\}(?:\s*\\label\{([^}]*)\})?",
            re.DOTALL,
        )

        # strip figures and equations first, process them separately
        figures_raw = figure_re.findall(tex)
        equations_raw = equation_re.findall(tex)

        tex_clean = figure_re.sub("<<FIGURE>>", tex)
        tex_clean = equation_re.sub("<<EQUATION>>", tex_clean)

        fig_iter = iter(figures_raw)
        eq_iter = iter(equations_raw)

        parts = re.split(
            r"(\\(?:sub)*section\*?\{[^}]+\}(?:\s*\\label\{[^}]*\})?|<<FIGURE>>|<<EQUATION>>)",
            tex_clean,
        )

        for part in parts:
            if not part.strip():
                continue

            sec_m = section_re.match(part.strip())
            if sec_m:
                level_map = {"section": 1, "subsection": 2, "subsubsection": 3}
                level = level_map.get(sec_m.group(1), 1)
                title = sec_m.group(2).strip()
                label = sec_m.group(3) or ""
                sec_id = label or re.sub(r"\W+", "_", title.lower())
                section = Section(id=sec_id, title=title, level=level)
                self._sections.append(section)
                self._current_section_id = sec_id
                continue

            if part.strip() == "<<FIGURE>>":
                try:
                    fig_content = next(fig_iter)
                    self._process_figure(fig_content, source_dir)
                except StopIteration:
                    pass
                continue

            if part.strip() == "<<EQUATION>>":
                try:
                    env, eq_body, eq_label = next(eq_iter)
                    self._process_equation(env, eq_body, eq_label)
                except StopIteration:
                    pass
                continue

            # plain text — clean and chunk
            text = self._clean_text(part)
            if text.strip():
                self._add_text_as_chunks(text)

    def _process_figure(self, content: str, source_dir: Path):
        self._fig_counter += 1

        label_m = re.search(r"\\label\{([^}]+)\}", content)
        label_key = label_m.group(1) if label_m else f"fig{self._fig_counter}"
        label_name = self._labels.get(label_key, f"Figure {self._fig_counter}")

        cap_m = re.search(r"\\caption\{(.*?)\}(?=\s*\\|$)", content, re.DOTALL)
        caption = self._clean_text(cap_m.group(1)) if cap_m else ""

        img_m = re.search(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", content)
        image_path = None
        if img_m:
            img_name = img_m.group(1)
            for ext in ["", ".pdf", ".png", ".jpg", ".jpeg", ".eps"]:
                candidate = source_dir / (img_name + ext)
                if candidate.exists():
                    image_path = str(candidate)
                    break

        ref = label_key
        self._figures.append(Figure(
            ref=ref,
            label=label_name,
            caption=caption,
            image_path=image_path,
            citing_chunk_id=self._chunk_counter,
        ))

        # inject a placeholder chunk so script gen can place figure_cue here
        cue_text = f"[Figure reference: {label_name}]"
        self._add_chunk(cue_text, ChunkType.CAPTION)

    def _process_equation(self, env: str, body: str, label_key: str):
        self._eq_counter += 1
        label_name = self._labels.get(label_key, f"Equation {self._eq_counter}") if label_key else f"Equation {self._eq_counter}"
        ref = label_key or f"eq{self._eq_counter}"

        self._equations.append(Equation(
            ref=ref,
            label=label_name,
            latex=body.strip(),
            is_inline=False,
            citing_chunk_id=self._chunk_counter,
        ))

        cue_text = f"[Equation reference: {label_name}]"
        self._add_chunk(cue_text, ChunkType.BODY)

    def _clean_text(self, text: str) -> str:
        # remove common macros
        text = re.sub(r"\\(textbf|textit|emph|text|mathrm|mathbf|mathit)\{([^}]+)\}", r"\2", text)
        text = re.sub(r"\\(footnote)\{[^}]*\}", "", text)
        text = re.sub(r"\\(cite|ref|label)\{[^}]*\}", "", text)
        text = re.sub(r"\\[a-zA-Z]+\*?\{([^}]*)\}", r"\1", text)
        text = re.sub(r"\\[a-zA-Z]+", " ", text)
        text = re.sub(r"[{}]", "", text)
        text = re.sub(r"%.*", "", text)  # LaTeX comments
        text = re.sub(r"\s+", " ", text)
        return text.strip()


def parse_latex(source_dir: Path, paper_id: str) -> DocumentModel:
    from ..ingest.arxiv import find_main_tex

    main_tex = find_main_tex(source_dir)
    if main_tex is None:
        raise ValueError(f"No .tex file found in {source_dir}")

    tex_source = main_tex.read_text(errors="ignore")

    # inline \input and \include
    tex_source = _inline_inputs(tex_source, main_tex.parent)

    parser = LatexParser()
    return parser.parse(tex_source, paper_id, source_dir)


def _inline_inputs(tex: str, base_dir: Path) -> str:
    def replacer(m):
        fname = m.group(1)
        for ext in ["", ".tex"]:
            path = base_dir / (fname + ext)
            if path.exists():
                return _inline_inputs(path.read_text(errors="ignore"), base_dir)
        return ""

    tex = re.sub(r"\\input\{([^}]+)\}", replacer, tex)
    tex = re.sub(r"\\include\{([^}]+)\}", replacer, tex)
    return tex
