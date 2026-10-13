"""Knowledge base: loads local articles (Markdown/TXT) plus Dynamics 365
knowledge articles, and retrieves the most relevant ones with BM25.

Local files can carry simple front matter so parts can be suggested accurately:

    ---
    title: ThinkPad T14 Gen 3 - No power / no POST
    models: T14 Gen 3, T14s Gen 3
    parts: 5B21H... (system board), 5B10W... (battery)
    ---
    body text...
"""
from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

_TOKEN = re.compile(r"[a-z0-9]+(?:[-.][a-z0-9]+)*")
_STOP = set(
    "a an and are as at be but by can do does for from has have i if in is it its my of on or so "
    "that the then this to was we were what when with you your it's im just like get got".split()
)


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP and len(t) > 1]


@dataclass
class Article:
    id: str
    title: str
    body: str
    source: str  # "local" or "dynamics"
    meta: dict = field(default_factory=dict)

    def snippet(self, limit: int = 1800) -> str:
        return self.body if len(self.body) <= limit else self.body[:limit] + " …"


def _parse_front_matter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    meta = {}
    for line in text[3:end].strip().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip().lower()] = v.strip()
    return meta, text[end + 4 :].lstrip()


class KnowledgeBase:
    def __init__(self) -> None:
        self.articles: list[Article] = []
        self._tf: list[Counter] = []
        self._df: Counter = Counter()
        self._avgdl = 0.0

    # ---------- loading ----------
    def load_folder(self, folder: str) -> int:
        path = Path(folder)
        if not path.exists():
            log.warning("KB folder %s not found", folder)
            return 0
        n = 0
        for f in sorted(path.rglob("*")):
            if f.suffix.lower() not in {".md", ".txt"}:
                continue
            meta, body = _parse_front_matter(f.read_text(encoding="utf-8", errors="ignore"))
            title = meta.get("title") or f.stem.replace("_", " ")
            self.articles.append(Article(id=f"local:{f.name}", title=title, body=body, source="local", meta=meta))
            n += 1
        self._reindex()
        return n

    def add_articles(self, articles: list[Article]) -> None:
        self.articles.extend(articles)
        self._reindex()

    def _reindex(self) -> None:
        self._tf, self._df = [], Counter()
        for a in self.articles:
            # Title and metadata (models/parts/symptoms) are weighted higher than body text.
            boosted = " ".join([a.title] * 3 + [" ".join(a.meta.values())] * 2 + [a.body])
            tf = Counter(tokenize(boosted))
            self._tf.append(tf)
            self._df.update(tf.keys())
        self._avgdl = sum(sum(t.values()) for t in self._tf) / max(len(self._tf), 1)

    # ---------- search ----------
    def search(self, query: str, k: int = 4, k1: float = 1.5, b: float = 0.75) -> list[tuple[Article, float]]:
        q = tokenize(query)
        if not q or not self.articles:
            return []
        N = len(self.articles)
        scores = []
        for art, tf in zip(self.articles, self._tf):
            dl = sum(tf.values())
            s = 0.0
            for term in set(q):
                if term not in tf:
                    continue
                idf = math.log(1 + (N - self._df[term] + 0.5) / (self._df[term] + 0.5))
                s += idf * tf[term] * (k1 + 1) / (tf[term] + k1 * (1 - b + b * dl / self._avgdl))
            if s > 0:
                scores.append((art, s))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:k]
