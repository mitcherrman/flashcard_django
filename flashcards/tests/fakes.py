"""
Test doubles for the backend regression suite.

* Synthetic PDFs are built in memory with PyMuPDF; every page carries unique
  "Fact P<page>-<n>: ..." sentences so any text that reaches the model can be
  traced back to the page it came from.
* FakeOpenAI stands in for the OpenAI client at the two seams the pipeline
  uses (templater.OpenAI and flashcard_gen.CLIENT). It answers like the real
  model would (JSON in message.content), records every request, and never
  touches the network.
"""
from __future__ import annotations

import json
import re
import threading
from collections import defaultdict
from types import SimpleNamespace

import fitz  # PyMuPDF

FACT_RE = re.compile(r"Fact P(\d+)-(\d+): ([^.\n]+)\.")


def fact_line(page: int, n: int) -> str:
    return f"Fact P{page}-{n}: The marker for page {page} item {n} is token{page}x{n}."


def make_pdf(page_count: int, *, toc=None, facts_per_page: int = 3) -> bytes:
    """PDF bytes with `facts_per_page` traceable facts on every page."""
    doc = fitz.open()
    for p in range(1, page_count + 1):
        page = doc.new_page()
        lines = [fact_line(p, n) for n in range(1, facts_per_page + 1)]
        page.insert_text((72, 72), "\n".join(lines), fontsize=11)
    if toc:
        doc.set_toc(toc)
    data = doc.tobytes()
    doc.close()
    return data


def fact_pages(text: str) -> set[int]:
    return {int(m.group(1)) for m in FACT_RE.finditer(text or "")}


def _after(marker: str, text: str) -> str:
    i = text.find(marker)
    return text[i + len(marker):] if i != -1 else ""


class FakeOpenAI:
    """Deterministic stand-in for `openai.OpenAI` (chat.completions only)."""

    def __init__(self, *, notoc_titles: dict[int, str] | None = None):
        self.calls: list[dict] = []
        self.notoc_titles = notoc_titles or {}
        self._served: dict[tuple, int] = defaultdict(int)
        self._lock = threading.Lock()
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    # ---- request routing -------------------------------------------------
    def _create(self, *, model, messages, **_kw):
        system = messages[0]["content"]
        user = messages[1]["content"]
        if "study-note generator" in system:
            kind, payload = "template", self._template(user)
        elif "flash-card author" in system:
            kind, payload = "cards", self._cards(system, user)
        else:
            raise AssertionError(f"unexpected prompt: {system[:80]!r}")
        with self._lock:
            self.calls.append({"kind": kind, "user": user})
        content = json.dumps(payload)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    # ---- study template: 6 Q:A bullets per section, no facts copied -------
    def _template(self, user: str) -> dict:
        m = re.search(r'Single-section title hint: "(.+)"', user)
        body = _after("TEXT:\n", user)
        if m:
            title = m.group(1)
        else:
            pages = sorted(fact_pages(body))
            first = pages[0] if pages else 0
            title = self.notoc_titles.get(first, f"Topic {first}")
        bullets = [{"q": f"Template question {k} for {title}", "a": f"template answer {k}"}
                   for k in range(1, 7)]
        return {"sections": [{"title": title, "bullets": bullets}]}

    # ---- cards: one per fact in the input, in text order -----------------
    def _cards(self, system: str, user: str) -> dict:
        max_cards = int(re.search(r'Max items in "cards": (\d+)', system).group(1))
        page_m = re.search(r"^PAGE: (\d+)$", user, re.M)
        section_m = re.search(r"^SECTION: (.+)$", user, re.M)
        section = section_m.group(1) if section_m else None
        body = _after("\nTEXT:\n", user)

        facts = [(m.group(0), m.group(1), m.group(2), m.group(3)) for m in FACT_RE.finditer(body)]
        if facts:
            pool = [
                {
                    "front": f"What is the marker for page {p} item {n}?",
                    "back": rest.rsplit(" is ", 1)[-1],
                    "excerpt": sentence,
                }
                for sentence, p, n, rest in facts
            ]
        else:
            # No page text (e.g. catch-up / estimated sections): use seed QA lines.
            pool = []
            for ln in _after("SEED QA LINES:\n", body).splitlines():
                if ": " in ln:
                    q, a = ln.split(": ", 1)
                    pool.append({"front": q.strip(), "back": a.strip() + f" ({q.strip()})", "excerpt": ln.strip()})

        key = (section, body)
        with self._lock:
            start = self._served[key]
            picked = pool[start:start + max_cards]
            self._served[key] = start + len(picked)

        cards = []
        for c in picked:
            card = dict(c, distractors=["wrong a", "wrong b", c["back"]], context="concept")
            if page_m:  # the real model echoes the PAGE indicator it was given
                card["page"] = int(page_m.group(1))
            if section:
                card["section"] = section
            cards.append(card)
        return {"cards": cards}

    # ---- helpers for assertions ------------------------------------------
    def card_inputs_by_section(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = defaultdict(list)
        for c in self.calls:
            if c["kind"] == "cards":
                m = re.search(r"^SECTION: (.+)$", c["user"], re.M)
                out[m.group(1) if m else ""].append(c["user"])
        return out

    def template_inputs_by_hint(self) -> dict[str, str]:
        out = {}
        for c in self.calls:
            if c["kind"] == "template":
                m = re.search(r'Single-section title hint: "(.+)"', c["user"])
                out[m.group(1) if m else ""] = c["user"]
        return out
