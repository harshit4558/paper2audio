from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from ..config import RSSConfig
from ..parse.models import DocumentModel


def _duration_str(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def update_feed(
    doc: DocumentModel,
    mp3_path: Path,
    duration_seconds: float,
    base_url: str,
    rss_path: Path,
    cfg: RSSConfig,
):
    rss_path.parent.mkdir(parents=True, exist_ok=True)

    if rss_path.exists():
        ET.register_namespace("itunes", "http://www.itunes.com/dtds/podcast-1.0.dtd")
        try:
            tree = ET.parse(rss_path)
            root = tree.getroot()
            channel = root.find("channel")
        except ET.ParseError:
            root, channel = _new_feed(cfg)
    else:
        root, channel = _new_feed(cfg)

    itunes = "http://www.itunes.com/dtds/podcast-1.0.dtd"

    # check if episode already exists
    for item in channel.findall("item"):
        guid = item.findtext("guid")
        if guid == doc.paper_id:
            return  # already in feed

    item = ET.SubElement(channel, "item")
    ET.SubElement(item, "title").text = doc.title
    ET.SubElement(item, "guid").text = doc.paper_id
    ET.SubElement(item, "pubDate").text = datetime.now(timezone.utc).strftime(
        "%a, %d %b %Y %H:%M:%S %z"
    )
    authors_str = ", ".join(doc.authors[:3]) + (" et al." if len(doc.authors) > 3 else "")
    ET.SubElement(item, "description").text = (
        f"{authors_str}. Processed with paper2audio."
    )

    mp3_url = f"{base_url.rstrip('/')}/audio/{doc.paper_id}/paper.mp3"
    mp3_size = mp3_path.stat().st_size if mp3_path.exists() else 0
    enc = ET.SubElement(item, "enclosure")
    enc.set("url", mp3_url)
    enc.set("type", "audio/mpeg")
    enc.set("length", str(mp3_size))

    ET.SubElement(item, f"{{{itunes}}}duration").text = _duration_str(duration_seconds)
    ET.SubElement(item, f"{{{itunes}}}explicit").text = "no"

    ET.indent(root, space="  ")
    rss_path.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))


def _new_feed(cfg: RSSConfig):
    itunes = "http://www.itunes.com/dtds/podcast-1.0.dtd"
    ET.register_namespace("itunes", itunes)
    root = ET.Element("rss", version="2.0")
    root.set("xmlns:itunes", itunes)
    channel = ET.SubElement(root, "channel")
    ET.SubElement(channel, "title").text = cfg.title
    ET.SubElement(channel, "description").text = cfg.description
    ET.SubElement(channel, "language").text = "en-us"
    ET.SubElement(channel, f"{{{itunes}}}author").text = cfg.author
    ET.SubElement(channel, f"{{{itunes}}}category").set("text", "Education")
    return root, channel
