"""Find what you repeat, and rank it as automation candidates.

Five kinds of pattern, each from the timeline in ``events``:

- **loops**: flipping back and forth between two things (a terminal session and a
  web page, say), often a sign one should come to the other;
- **habits**: sites you open on most days, usually at the same hours (a digest
  could check them for you);
- **carries**: copying in one app and switching straight to another, i.e. moving
  text by hand between apps that could talk to each other;
- **searches**: the same web search on different days (a saved search or watch);
- **asks**: what you keep typing to AI assistants: the kind of request
  ("keep going", "what's next", "fix ...") and near-identical prompts repeated
  across days (a command, skill or scheduled job).

Each candidate carries a stable ``id`` (so a weekly run can tell new from known),
the evidence behind it, a rough minutes-per-week cost with how it was estimated,
and a generic suggestion. Prompt and clipboard text is only included when
``include_examples`` is set; by default the report holds counts, apps, sites and
session names.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from .events import Events, site_label

# --- tunables ----------------------------------------------------------------------
LOOP_GAP_S = 180            # A, B, A within 3 minutes counts as one flip back
MIN_LOOP_FLIPS = 12         # per report window
MIN_CHAIN = 6
MIN_CHAIN_DAYS = 3
HABIT_MIN_DAYS = 5
HABIT_MIN_SHARE = 0.35      # of the days you were active
MIN_CARRIES = 8
MIN_SEARCH_DAYS = 2
MIN_SEARCH_HITS = 3
MIN_INTENT_PROMPTS = 8
MIN_INTENT_DAYS = 3
MIN_REPEAT = 3
REPEAT_SIMILARITY = 0.6
NUDGE_MAX_WORDS = 8         # a go-ahead is short; longer prompts starting "ok ..." are requests

# Sites that are plumbing rather than a destination, or calls rather than check-ins.
_NOT_HABITS = {"google.com", "accounts.google.com", "newtab", "localhost", "127.0.0.1",
               "login.microsoftonline.com", "duckduckgo.com", "bing.com",
               "meet.google.com", "zoom.us", "teams.microsoft.com", "whereby.com"}
# A site you spend longer than this on per day is where you work, not something you check.
CHECK_IN_MAX_MIN_PER_DAY = 15.0
_SEARCH_HOSTS = ("google.", "bing.com", "duckduckgo.com", "search.brave.com", "ecosia.org")

# Generic kinds of request people make to AI assistants, with what tends to remove them.
INTENTS: list[tuple[str, str, str, str]] = [
    ("nudge", "telling the assistant to carry on (\"keep going\", \"do it\")",
     r"^(keep going|do it|go( ahead)?|yes|yep|ok(ay)?|continue|carry on|proceed|send it|ship it|"
     r"approved?|merge it|do (it |them |this )?all|do both)\b",
     "Let routine steps run without asking, and get one summary instead of approving each step."),
    ("status", "asking how things are going or what's next",
     r"\b(what('?s| is)? next|where are we|status|what('?s| is) left|progress|how('?s| is) it going|"
     r"hows (it|things|progress))\b",
     "A scheduled status digest across the work in flight."),
    ("check-messages", "asking it to check your messages",
     r"\bcheck (discord|whatsapp|slack|teams|my (mail|email|inbox)|messages)\b",
     "A watcher that reads new messages and only pings you when something needs you."),
    ("draft-send", "drafting or sending messages",
     r"\b(reply|respond|draft|follow ?up|send (a |an )?(message|email|note|whatsapp))\b",
     "An approve-then-send path, so drafts go out without being copied by hand."),
    ("fix", "reporting something broken",
     r"\b(fix|broken|not working|doesn'?t work|isn'?t working|error|failing|crash(ed|es)?|bug)\b",
     "Health checks that catch the breakage before you do."),
    ("deploy", "shipping and deploying",
     r"\b(deploy|push (it|to|the)|commit|merge|release|publish|testflight)\b",
     "A one-step release (script or CI) for the projects you ship most."),
    ("resume", "resuming after a restart",
     r"\b(resume|restart(ed|ing)?|closed (it|the|by)|where was i|lost (the|my))\b",
     "Automatic session snapshots and restore."),
    ("schedule", "scheduling and reminders",
     r"\b(remind|schedule|overnight|every (day|morning|week|hour)|tomorrow at)\b",
     "Standing schedules for work you queue up repeatedly."),
    ("lookup", "looking people and things up",
     r"^(who('?s| is)|what('?s| is)|look up|find out|research)\b",
     "Briefs prepared ahead: new names in your calendar and mail looked up automatically."),
    ("meeting", "meeting prep and follow-up",
     r"\b(prep|brief|agenda|meeting|call with|transcript|recording)\b",
     "A meeting pipeline: prep before, notes and follow-ups after, without asking."),
    ("slides", "working on decks and documents",
     r"\b(deck|slides?|presentation|google doc)\b",
     "Keep the assistant in sync with the live document so it sees your edits."),
]
_INTENT_RX = [(key, label, re.compile(rx, re.IGNORECASE), tip) for key, label, rx, tip in INTENTS]


# --- small helpers -------------------------------------------------------------------

def _cid(kind: str, key: str) -> str:
    return hashlib.sha1(f"{kind}|{key}".encode("utf-8")).hexdigest()[:10]


def _norm(text: str) -> str:
    t = re.sub(r"[^\w\s']", " ", text.lower())
    return " ".join(t.split())


def _trigrams(text: str) -> set[str]:
    t = f"  {_norm(text)}  "
    return {t[i:i + 3] for i in range(len(t) - 2)}


def similarity(a: str, b: str) -> float:
    """Character-trigram Jaccard: robust to typos and small rewordings."""
    ta, tb = _trigrams(a), _trigrams(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


def cluster(texts: list[str], threshold: float) -> list[list[int]]:
    """Greedy single-pass clusters of near-identical texts (indices into ``texts``)."""
    reps: list[tuple[set[str], list[int]]] = []
    for i, t in enumerate(texts):
        grams = _trigrams(t)
        for rep, members in reps:
            if grams and rep and len(grams & rep) / len(grams | rep) >= threshold:
                members.append(i)
                break
        else:
            reps.append((grams, [i]))
    return [m for _, m in reps]


def clip_kind(text: str) -> str:
    t = (text or "").strip()
    if re.fullmatch(r"https?://\S+", t):
        return "link"
    if "\n" not in t and re.match(r"^(cd|ls|git|uv|npm|npx|python3?|pip|ssh|open|brew|curl|docker|make|"
                                  r"kubectl|claude|code)\b", t):
        return "command"
    if re.search(r"[{};]\s*$|^\s*(def|import|class|function|const|let|var)\b", t, re.M):
        return "code"
    if len(t) < 40 and "\n" not in t:
        return "snippet"
    return "text"


def _app_of(context: str | None) -> str | None:
    return context.split(" · ", 1)[0] if context else None


def _hours(times: list[datetime], n: int = 2) -> list[int]:
    return [h for h, _ in Counter(t.hour for t in times).most_common(n)]


def _per_week(count: float, ev: Events) -> float:
    weeks = max(1.0, (ev.end - ev.start).days / 7.0)
    return count / weeks


def _search_query(url: str) -> str | None:
    try:
        p = urlparse(url)
    except ValueError:
        return None
    host = p.netloc.lower()
    if not any(h in host for h in _SEARCH_HOSTS):
        return None
    q = parse_qs(p.query).get("q", [""])[0].strip()
    return _norm(q) or None


# --- the miner ---------------------------------------------------------------------

def mine(ev: Events, *, include_examples: bool = False) -> dict:
    """The full report for a timeline. Pure: no database, no clock."""
    loops = _loops(ev)
    chains = _chains(ev)
    habits = _habits(ev)
    carries = _carries(ev, include_examples)
    searches = _searches(ev, include_examples)
    asks = _asks(ev, include_examples)

    candidates: list[dict] = []
    for lp in loops:
        candidates.append({
            "id": _cid("loop", "|".join(lp["pair"])), "kind": "loop",
            "title": f"Flipping between {lp['pair'][0]} and {lp['pair'][1]}",
            "evidence": {"flips": lp["flips"], "days": lp["days"], "minutes": lp["minutes"]},
            "weekly_minutes": round(_per_week(lp["minutes"], ev), 1),
            "estimate": "time spent on the second side during back-and-forth flips",
            "suggestion": "Bring one into the other: have the agent read or update the page itself, "
                          "or show the result where you already are.",
        })
    for ch in chains:
        candidates.append({
            "id": _cid("chain", "|".join(ch["sequence"])), "kind": "routine",
            "title": "Repeated sequence: " + " → ".join(ch["sequence"]),
            "evidence": {"times": ch["count"], "days": ch["days"], "usual_hours": ch["hours"]},
            "weekly_minutes": round(_per_week(ch["count"] * ch["avg_minutes"], ev), 1),
            "estimate": "average length of the sequence × how often it happens",
            "suggestion": "A single command or scheduled job that runs the sequence.",
        })
    for hb in habits:
        candidates.append({
            "id": _cid("habit", hb["site"]), "kind": "habit",
            "title": f"Checking {hb['site']} on {hb['days']} of {ev.days} days",
            "evidence": {"days": hb["days"], "visits": hb["visits"], "usual_hours": hb["hours"]},
            "weekly_minutes": round(_per_week(hb["days"] * max(hb["minutes_per_day"], 2.0), ev), 1),
            "estimate": "focus time on the site per day (at least 2 minutes) × days",
            "suggestion": "Fold it into a digest that checks it for you at the time you usually look.",
        })
    for ca in carries:
        candidates.append({
            "id": _cid("carry", f"{ca['from']}>{ca['to']}:{ca['kind']}"), "kind": "carry",
            "title": f"Copying {ca['kind']} from {ca['from']} into {ca['to']} by hand",
            "evidence": {"carries": ca["count"], "days": ca["days"]},
            "weekly_minutes": round(_per_week(ca["count"] * 1.0, ev), 1),
            "estimate": "1 minute per copy-and-switch",
            "suggestion": f"Send it from {ca['from']} straight to {ca['to']} (an integration or an "
                          "approve-then-send step) instead of copying.",
            **({"examples": ca["examples"]} if include_examples else {}),
        })
    for se in searches:
        what = f"\"{se['query']}\"" if include_examples else "the same thing"
        candidates.append({
            "id": _cid("search", se["_key"]), "kind": "search",
            "title": f"Searching {what} again on {se['days']} days",
            "evidence": {"days": se["days"], "searches": se["hits"], "variants": se["variants"]},
            "weekly_minutes": round(_per_week(se["hits"] * 1.5, ev), 1),
            "estimate": "1.5 minutes per repeated search",
            "suggestion": "A saved search or a watch that tells you when something changes.",
        })
    for it in asks["by_intent"]:
        if it["prompts"] < MIN_INTENT_PROMPTS or it["days"] < MIN_INTENT_DAYS:
            continue
        candidates.append({
            "id": _cid("ask-intent", it["intent"]), "kind": "ask-intent",
            "title": f"{it['prompts']} prompts {it['label']}",
            "evidence": {"prompts": it["prompts"], "days": it["days"], "usual_hours": it["hours"]},
            "weekly_minutes": round(_per_week(it["prompts"] * 1.0, ev), 1),
            "estimate": "1 minute per prompt (typing, waiting, switching back)",
            "suggestion": it["suggestion"],
            **({"examples": it["examples"]} if include_examples else {}),
        })
    for rp in asks["repeated"]:
        candidates.append({
            "id": _cid("ask-repeat", rp["_key"]), "kind": "ask-repeat",
            "title": f"The same request {rp['count']} times on {rp['days']} days",
            "evidence": {"times": rp["count"], "days": rp["days"], "projects": rp["projects"]},
            "weekly_minutes": round(_per_week(rp["count"] * 1.5, ev), 1),
            "estimate": "1.5 minutes per repeat",
            "suggestion": "Make it a command or skill, or a scheduled job if it's time-driven.",
            **({"examples": rp["examples"]} if include_examples else {}),
        })
    candidates.sort(key=lambda c: c["weekly_minutes"], reverse=True)
    for section in (searches, asks["repeated"]):
        for item in section:
            item.pop("_key", None)

    return {
        "window": {"start": ev.start.isoformat(), "end": ev.end.isoformat(), "active_days": ev.days,
                   "focus_source": ev.focus_source},
        "summary": _summary(ev),
        "loops": loops, "routines": chains, "habits": habits, "carries": carries,
        "searches": searches, "asks": asks,
        "candidates": candidates,
    }


def _summary(ev: Events) -> dict:
    apps: Counter[str] = Counter()
    contexts: Counter[str] = Counter()
    for sp in ev.spans:
        apps[sp.app] += sp.seconds
        contexts[sp.context] += sp.seconds
    return {
        "focus_hours": round(sum(apps.values()) / 3600, 1),
        "switches": len(ev.spans),
        "top_apps": [{"app": a, "hours": round(s / 3600, 1)} for a, s in apps.most_common(10)],
        "top_contexts": [{"context": c, "hours": round(s / 3600, 1)} for c, s in contexts.most_common(15)],
        "typed_prompts": len(ev.asks),
        "clipboard_copies": len(ev.clips),
        "browser_visits": len(ev.visits),
    }


def _loops(ev: Events) -> list[dict]:
    flips: Counter[tuple[str, str]] = Counter()
    days: dict[tuple[str, str], set] = defaultdict(set)
    secs: Counter[tuple[str, str]] = Counter()
    sp = ev.spans
    for i in range(2, len(sp)):
        a, b, c = sp[i - 2], sp[i - 1], sp[i]
        if a.context != c.context or b.context == a.context:
            continue
        if (c.start - a.end).total_seconds() > LOOP_GAP_S:
            continue
        pair = tuple(sorted((a.context, b.context)))
        flips[pair] += 1
        days[pair].add(c.start.date())
        secs[pair] += b.seconds
    return [
        {"pair": list(p), "flips": n, "days": len(days[p]), "minutes": round(secs[p] / 60, 1)}
        for p, n in flips.most_common(15) if n >= MIN_LOOP_FLIPS
    ]


def _chains(ev: Events) -> list[dict]:
    seen: Counter[tuple[str, ...]] = Counter()
    days: dict[tuple, set] = defaultdict(set)
    when: dict[tuple, list] = defaultdict(list)
    dur: Counter[tuple] = Counter()
    sp = ev.spans
    for i in range(2, len(sp)):
        a, b, c = sp[i - 2], sp[i - 1], sp[i]
        if len({a.context, b.context, c.context}) < 3:
            continue  # loops are counted separately
        if (b.start - a.end).total_seconds() > 120 or (c.start - b.end).total_seconds() > 120:
            continue
        key = (a.context, b.context, c.context)
        seen[key] += 1
        days[key].add(a.start.date())
        when[key].append(a.start)
        dur[key] += (c.end - a.start).total_seconds()
    out = []
    for key, n in seen.most_common(30):
        if n < MIN_CHAIN or len(days[key]) < MIN_CHAIN_DAYS:
            continue
        out.append({"sequence": list(key), "count": n, "days": len(days[key]),
                    "hours": _hours(when[key]), "avg_minutes": round(dur[key] / n / 60, 1)})
    return out[:10]


def _habits(ev: Events) -> list[dict]:
    days: dict[str, set] = defaultdict(set)
    hits: Counter[str] = Counter()
    when: dict[str, list] = defaultdict(list)
    for v in ev.visits:
        site = site_label(v.url)
        if not site or site in _NOT_HABITS or site.split(":")[0] in _NOT_HABITS:
            continue
        days[site].add(v.at.date())
        hits[site] += 1
        when[site].append(v.at)
    focus: Counter[str] = Counter()
    for sp in ev.spans:
        if " · " in sp.context:
            focus[sp.context.split(" · ", 1)[1]] += sp.seconds
    out = []
    need = max(HABIT_MIN_DAYS, HABIT_MIN_SHARE * ev.days)
    for site, d in days.items():
        if len(d) < need:
            continue
        per_day = focus[site] / 60 / len(d)
        if per_day > CHECK_IN_MAX_MIN_PER_DAY:
            continue  # a workplace (see summary.top_contexts), not a check-in
        out.append({"site": site, "days": len(d), "visits": hits[site], "hours": _hours(when[site], 3),
                    "minutes_per_day": round(per_day, 1)})
    out.sort(key=lambda h: h["days"], reverse=True)
    return out[:15]


def _carries(ev: Events, include_examples: bool) -> list[dict]:
    count: Counter[tuple[str, str, str]] = Counter()
    days: dict[tuple, set] = defaultdict(set)
    ex: dict[tuple, list] = defaultdict(list)
    for c in ev.clips:
        src, dst = _app_of(c.source), _app_of(c.dest)
        if not src or not dst or src == dst:
            continue
        key = (src, dst, clip_kind(c.text))
        count[key] += 1
        days[key].add(c.at.date())
        if include_examples and len(ex[key]) < 3:
            ex[key].append(" ".join(c.text.split())[:160])
    return [
        {"from": k[0], "to": k[1], "kind": k[2], "count": n, "days": len(days[k]),
         **({"examples": ex[k]} if include_examples else {})}
        for k, n in count.most_common(15) if n >= MIN_CARRIES
    ]


def _searches(ev: Events, include_examples: bool) -> list[dict]:
    queries: list[tuple[str, datetime]] = []
    for v in ev.visits:
        q = _search_query(v.url)
        if q:
            queries.append((q, v.at))
    if not queries:
        return []
    distinct = sorted({q for q, _ in queries})
    groups = cluster(distinct, 0.5)
    by_q: dict[str, list[datetime]] = defaultdict(list)
    for q, at in queries:
        by_q[q].append(at)
    out = []
    for g in groups:
        qs = [distinct[i] for i in g]
        times = [t for q in qs for t in by_q[q]]
        d = {t.date() for t in times}
        if len(d) < MIN_SEARCH_DAYS or len(times) < MIN_SEARCH_HITS:
            continue
        rep = max(qs, key=lambda q: len(by_q[q]))
        item = {"_key": rep, "days": len(d), "hits": len(times), "variants": len(qs)}
        if include_examples:
            item["query"] = rep
        out.append(item)
    out.sort(key=lambda s: (s["days"], s["hits"]), reverse=True)
    return out[:15]


def _asks(ev: Events, include_examples: bool) -> dict:
    by: dict[str, dict] = {}
    for key, label, rx, tip in _INTENT_RX:
        by[key] = {"intent": key, "label": label, "suggestion": tip, "prompts": 0, "_days": set(),
                   "_times": [], "examples": []}
    for a in ev.asks:
        first = a.prompt.strip().split("\n", 1)[0]
        for key, _label, rx, _tip in _INTENT_RX:
            if key == "nudge" and len(first.split()) > NUDGE_MAX_WORDS:
                continue  # "ok - what do we need next to ..." is a question, not a go-ahead
            if rx.search(first):
                b = by[key]
                b["prompts"] += 1
                b["_days"].add(a.at.date())
                b["_times"].append(a.at)
                if include_examples and len(b["examples"]) < 3 and len(first) <= 160:
                    b["examples"].append(first)
    by_intent = []
    for b in by.values():
        if not b["prompts"]:
            continue
        item = {k: v for k, v in b.items() if not k.startswith("_")}
        item["days"] = len(b["_days"])
        item["hours"] = _hours(b["_times"])
        if not include_examples:
            item.pop("examples")
        by_intent.append(item)
    by_intent.sort(key=lambda i: i["prompts"], reverse=True)

    # Near-identical prompts across days (ignore the one-word nudges counted above).
    texts = [a.prompt.split("\n", 1)[0][:200] for a in ev.asks]
    idx = [i for i, t in enumerate(texts) if len(_norm(t).split()) >= 4]
    groups = cluster([texts[i] for i in idx], REPEAT_SIMILARITY)
    repeated = []
    for g in groups:
        members = [ev.asks[idx[i]] for i in g]
        d = {m.at.date() for m in members}
        if len(members) < MIN_REPEAT or len(d) < 2:
            continue
        rep = min((texts[idx[i]] for i in g), key=len)
        item = {"_key": _norm(rep)[:80], "count": len(members), "days": len(d),
                "projects": sorted({m.project for m in members if m.project})[:5]}
        if include_examples:
            item["examples"] = [rep[:160]]
        repeated.append(item)
    repeated.sort(key=lambda r: (r["count"], r["days"]), reverse=True)
    return {"typed": len(ev.asks), "by_intent": by_intent, "repeated": repeated[:15]}
