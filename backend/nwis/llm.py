"""Optional on-prem LLM (Ollama) for rephrasing grounded facts. Off by default.

Design rule: the LLM never retrieves or decides; it only rewrites facts we give it,
and every output sentence must carry a [n] citation to retrieved evidence or a
fact index, otherwise it is dropped (citation guard).
"""
from __future__ import annotations

import json
import re
import urllib.request

from . import config


def llm_status() -> dict:
    return {"backend": config.LLM_BACKEND, "model": config.OLLAMA_MODEL if config.LLM_BACKEND == "ollama" else None}


def citation_guard(text: str, n_cites: int, n_facts: int) -> str:
    kept = []
    for s in re.split(r"(?<=[.!?])\s+", text.strip()):
        refs = [int(x) for x in re.findall(r"\[(\d+)\]", s)]
        frefs = [int(x) for x in re.findall(r"\(F(\d+)\)", s)]
        if (refs and all(1 <= r <= n_cites for r in refs)) or (frefs and all(1 <= f <= n_facts for f in frefs)):
            kept.append(s)
    return " ".join(kept)


def llm_summarize(question: str, facts: list[str], cites: list[dict]) -> str | None:
    if config.LLM_BACKEND != "ollama":
        return None
    numbered = "\n".join(f"(F{i + 1}) {f}" for i, f in enumerate(facts))
    prompt = ("You are a drilling engineer's assistant. Answer the question using ONLY the numbered facts. "
              "End every sentence with the fact tag it relies on, e.g. (F2), or an evidence tag like [1] if present. "
              "Do not add any number that is not in the facts.\n\n"
              f"Question: {question}\n\nFacts:\n{numbered}\n\nAnswer in 3-5 sentences:")
    try:
        req = urllib.request.Request(f"{config.OLLAMA_URL}/api/generate",
                                     data=json.dumps({"model": config.OLLAMA_MODEL, "prompt": prompt,
                                                      "stream": False, "options": {"temperature": 0.1}}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            out = json.loads(r.read()).get("response", "")
    except Exception:
        return None
    guarded = citation_guard(out, len(cites), len(facts))
    return guarded or None
