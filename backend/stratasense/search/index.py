"""Hybrid retrieval: BM25 (lexical, with drilling synonym expansion) + LSA (semantic)
fused with Reciprocal Rank Fusion, then structured filters from the query parser."""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
from scipy import sparse
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer

from ..domain.ontology import FORMATION_BY_CODE, HAZARD_BY_CODE, MITIGATION_BY_CODE, SEARCH_SYNONYMS
from ..geo import haversine_km
from ..ingest.nlp import find_hazards
from ..kb import KnowledgeBase
from .query import ParsedQuery, parse_query

TOKEN = re.compile(r"[a-z0-9]+(?:[-/][a-z0-9]+)*")
STOP = set("the a an of in at on to for and or with from by is was were be been it this that as while after before "
           "into over per m ft hrs hr well".split())


def tokenize(text: str) -> list[str]:
    return [t for t in TOKEN.findall(text.lower()) if t not in STOP and not t.isdigit()]


@dataclass
class Doc:
    id: str
    type: str          # event | lesson | passage
    text: str
    well_id: str | None
    formation: str | None
    hazards: list[str]
    md: float | None
    year: int | None
    meta: dict


class SearchIndex:
    def __init__(self, kb: KnowledgeBase):
        self.kb = kb
        self.docs: list[Doc] = []
        self._build()

    def _build(self) -> None:
        kb = self.kb
        docs: list[Doc] = []
        for e in kb.events:
            w = kb.wells.get(e["well_id"])
            acts = ", ".join(MITIGATION_BY_CODE[a["code"]].label for a in e["actions"] if a["code"] in MITIGATION_BY_CODE)
            cit = " ".join(c["text"] for c in e["citations"][:4])
            fm = FORMATION_BY_CODE[e["formation"]].name if e["formation"] in FORMATION_BY_CODE else ""
            text = f"{HAZARD_BY_CODE[e['hazard']].label} {e['subtype']} {fm} {e['cause'] or ''}. {e['summary']}. {acts}. {cit}"
            docs.append(Doc(e["id"], "event", text, e["well_id"], e["formation"], [e["hazard"]], e["md"],
                            w.spud_year if w else None, {"event": e}))
        for l in kb.lessons:
            w = kb.wells.get(l["well_id"])
            hz = [h.hazard for h in find_hazards(l["text"])]
            docs.append(Doc(l["id"], "lesson", l["text"], l["well_id"], l["formation"],
                            sorted(set(([l["hazard"]] if l["hazard"] else []) + hz)), None, w.spud_year if w else None,
                            {"lesson": l}))
        # passages: time-log records of every page (things the extractor did not turn into events)
        rows = kb.db.query("SELECT p.doc_id, p.page_no, p.text, d.well_id, d.title, d.kind FROM pages p "
                           "JOIN documents d ON d.id=p.doc_id")
        for r in rows:
            off = 0
            for line in r["text"].splitlines(keepends=True):
                s = line.strip()
                start = off
                off += len(line)
                if len(s) < 40 or s.upper().startswith(("STRATASENSE DEMO", "WELL:", "DEPTH @", "MUD:", "HOLE SIZE")):
                    continue
                hz = sorted({h.hazard for h in find_hazards(s) if not h.negated})
                w = kb.wells.get(r["well_id"])
                docs.append(Doc(f"{r['doc_id']}:{r['page_no']}:{start}", "passage", s, r["well_id"], None, hz, None,
                                w.spud_year if w else None,
                                {"doc_id": r["doc_id"], "page_no": r["page_no"], "start": start + (len(line) - len(line.lstrip())),
                                 "end": start + len(line.rstrip()), "title": r["title"], "kind": r["kind"]}))
        self.docs = docs
        texts = [d.text for d in docs]
        self.cv = CountVectorizer(tokenizer=tokenize, lowercase=False, token_pattern=None, ngram_range=(1, 2), min_df=1)
        tf = self.cv.fit_transform(texts).tocsc().astype(np.float32)
        dl = np.asarray(tf.sum(axis=1)).ravel()
        self.avgdl = float(dl.mean()) if len(dl) else 1.0
        df = np.asarray((tf > 0).sum(axis=0)).ravel()
        n = len(docs)
        self.idf = np.log(1 + (n - df + 0.5) / (df + 0.5))
        k1, b = 1.4, 0.75
        tf = tf.tocoo()
        denom = tf.data + k1 * (1 - b + b * dl[tf.row] / self.avgdl)
        bm = tf.data * (k1 + 1) / denom
        self.bm25 = sparse.csc_matrix((bm, (tf.row, tf.col)), shape=tf.shape)
        self.vocab = self.cv.vocabulary_
        self.tfidf = TfidfVectorizer(tokenizer=tokenize, lowercase=False, token_pattern=None, sublinear_tf=True, min_df=2)
        X = self.tfidf.fit_transform(texts)
        k = min(128, X.shape[1] - 1, X.shape[0] - 1)
        self.svd = TruncatedSVD(n_components=max(k, 2), random_state=0)
        E = self.svd.fit_transform(X)
        self.emb = E / (np.linalg.norm(E, axis=1, keepdims=True) + 1e-9)
        self.types = np.array([d.type for d in docs])

    # --------------------------------------------------------------- scoring
    def _bm25_scores(self, terms: dict[str, float]) -> np.ndarray:
        s = np.zeros(len(self.docs), dtype=np.float32)
        for t, w in terms.items():
            j = self.vocab.get(t)
            if j is None:
                continue
            col = self.bm25.getcol(j)
            s[col.indices] += w * self.idf[j] * col.data
        return s

    def _expand(self, text: str) -> dict[str, float]:
        toks = tokenize(text)
        terms: dict[str, float] = {}
        for t in toks:
            terms[t] = max(terms.get(t, 0), 1.0)
            for syn in SEARCH_SYNONYMS.get(t, ()):
                for st in tokenize(syn):
                    terms[st] = max(terms.get(st, 0), 0.5)
        for a, b in zip(toks, toks[1:]):
            terms[f"{a} {b}"] = 1.5
        return terms

    def search(self, q: str | ParsedQuery, types: tuple[str, ...] = ("event", "lesson", "passage"), limit: int = 20,
               center: tuple[float, float] | None = None, extra_filters: dict | None = None) -> dict:
        pq = q if isinstance(q, ParsedQuery) else parse_query(q)
        text = pq.free_text or pq.text
        terms = self._expand(text)
        bm = self._bm25_scores(terms)
        qv = self.svd.transform(self.tfidf.transform([text]))
        qv = qv / (np.linalg.norm(qv) + 1e-9)
        sem = (self.emb @ qv.ravel()).astype(np.float32)
        mask = self._filter_mask(pq, types, center, extra_filters or {})
        idx = np.where(mask)[0]
        if len(idx) == 0:
            return {"query": pq.to_dict(), "results": [], "total": 0}
        r_bm = np.argsort(-bm[idx], kind="stable")
        r_sem = np.argsort(-sem[idx], kind="stable")
        rrf = np.zeros(len(idx))
        rrf[r_bm] += 1.0 / (60 + np.arange(len(idx)))
        rrf[r_sem] += 1.0 / (60 + np.arange(len(idx)))
        # structured boosts: parsed hazard / formation agreement, events & lessons first
        boost = np.zeros(len(idx))
        for k, i in enumerate(idx):
            d = self.docs[i]
            if pq.hazards and set(pq.hazards) & set(d.hazards):
                boost[k] += 0.01
            if pq.formation and d.formation == pq.formation:
                boost[k] += 0.006
            boost[k] += {"event": 0.004, "lesson": 0.003, "passage": 0.0}[d.type]
            if bm[i] == 0 and sem[i] < 0.15:
                boost[k] -= 0.02
        score = rrf + boost
        order = idx[np.argsort(-score)][:limit]
        sc = dict(zip(idx, score))
        results = [self._result(self.docs[i], float(sc[i]), terms, bm[i], sem[i]) for i in order]
        return {"query": pq.to_dict(), "results": results, "total": int(mask.sum())}

    def _filter_mask(self, pq: ParsedQuery, types, center, extra) -> np.ndarray:
        kb = self.kb
        allowed_wells = None
        if pq.radius_km or pq.near_well:
            if pq.near_well and pq.near_well in kb.wells and kb.wells[pq.near_well].located:
                c = (kb.wells[pq.near_well].lat, kb.wells[pq.near_well].lon)
            else:
                c = center
            if c:
                r = pq.radius_km or 5.0
                allowed_wells = {w.id for w in kb.wells.values() if w.located and float(haversine_km(c[0], c[1], w.lat, w.lon)) <= r}
        if pq.wells:
            allowed_wells = set(pq.wells) if allowed_wells is None else allowed_wells & set(pq.wells)
        mask = np.zeros(len(self.docs), dtype=bool)
        for i, d in enumerate(self.docs):
            if d.type not in types:
                continue
            if allowed_wells is not None and d.well_id not in allowed_wells:
                continue
            if pq.hazards and d.type != "passage" and not (set(pq.hazards) & set(d.hazards)):
                continue
            if pq.hazards and d.type == "passage" and not (set(pq.hazards) & set(d.hazards)):
                continue
            if pq.formation and d.type == "event" and d.formation != pq.formation:
                continue
            if pq.formation and d.type == "lesson" and d.formation not in (None, pq.formation):
                continue
            if pq.md_min is not None and (d.md is None and d.type == "event" or d.md is not None and d.md < pq.md_min):
                continue
            if pq.md_max is not None and d.md is not None and d.md > pq.md_max:
                continue
            if pq.year_min and (d.year or 0) < pq.year_min:
                continue
            if pq.year_max and (d.year or 9999) > pq.year_max:
                continue
            if extra.get("well_id") and d.well_id != extra["well_id"]:
                continue
            mask[i] = True
        return mask

    def _result(self, d: Doc, score: float, terms: dict[str, float], bm: float, sem: float) -> dict:
        hl = []
        low = d.text.lower()
        for t in terms:
            if " " in t or len(t) < 3:
                continue
            for m in re.finditer(r"(?<![a-z0-9])" + re.escape(t), low):
                hl.append([m.start(), m.end()])
        out = {"id": d.id, "type": d.type, "text": d.text, "well_id": d.well_id, "formation": d.formation,
               "hazards": d.hazards, "md": d.md, "year": d.year, "score": round(score, 5),
               "lexical": round(float(bm), 3), "semantic": round(float(sem), 3), "highlights": sorted(hl)[:40]}
        if d.type == "event":
            e = d.meta["event"]
            out["event"] = {k: e[k] for k in ("id", "hazard", "subtype", "md", "formation", "severity", "npt_hours",
                                              "summary", "confidence", "cause")}
            out["citation"] = e["citations"][0] if e["citations"] else None
            out["text"] = e["summary"]
            out["highlights"] = []
        elif d.type == "lesson":
            l = d.meta["lesson"]
            out["citation"] = {"doc_id": l["doc_id"], "page_no": l["page_no"], "start": l["start"], "end": l["end"],
                               "title": l.get("doc_title"), "text": l["text"]}
        else:
            out["citation"] = d.meta
        return out


_INDEX: SearchIndex | None = None


def get_index(kb: KnowledgeBase) -> SearchIndex:
    global _INDEX
    if _INDEX is None or _INDEX.kb is not kb:
        _INDEX = SearchIndex(kb)
    return _INDEX


def reset_index() -> None:
    global _INDEX
    _INDEX = None
