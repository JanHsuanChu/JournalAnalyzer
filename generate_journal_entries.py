# generate_journal_entries.py
# Synthetic journal data for development and demos.
# Regenerates journal_entries.csv: N entries spread across the last 12 months with varied text.

# 0. Setup #################################

from __future__ import annotations

import argparse
import random
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

# Reproducible but varied text; change to get a different sample of combinations
DEFAULT_SEED = 20260404

# 1. Text building blocks #################################
# Target: ~50–200 words per entry. Themes mixed across sentences: mental health (varied
# language—not only clinical terms), energy and productivity, habits and goals, social life and travel.

_MIN_WORDS = 50
_MAX_WORDS = 200

# Mental health & inner life (clinical terms sometimes; more often plain descriptions)
_SENT_MENTAL = [
    "The low mood wasn't dramatic, more like a thin fog that made everything take extra effort and color felt a bit drained out.",
    "My mind kept jumping to worst-case scenarios before breakfast, that familiar tight chest and shallow breath I used to call 'just stress.'",
    "I've been describing the week to my therapist as a slow slide—not a crisis, more like losing traction on things that usually steady me.",
    "Attention splintered all afternoon: tabs, notifications, half-finished sentences in my head, the scattered focus people sometimes label ADD-ish when they're being casual.",
    "I caught myself going back to check the stove and the lock again, hating how automatic it felt, like my brain offering certainty in exchange for time I'll never get back.",
    "Depression doesn't always look like crying in bed; sometimes it's answering messages with one word and feeling proud you answered at all.",
    "Anxiety sat on my shoulders through the meeting—not panic, just a hum that made it hard to track who was speaking unless I really strained.",
    "OCD isn't only washing hands in the movies; for me it's re-reading emails until the meaning warps and I still can't hit send without a spike of doubt.",
    "I felt numb in a way that worried me less than it used to, which might mean I'm getting used to it or that I'm finally naming it instead of pretending I'm fine.",
    "Rumination loop: one awkward moment from Tuesday replayed in HD while I brushed my teeth, as if my brain thought reviewing it would change the past.",
    "Sleep was shallow; I woke at three with a racing heart and told myself it was caffeine, not fear, though I'm not sure I believed it.",
    "Guilt stacked up over small things I didn't do—calling someone back, sorting mail—until the pile felt like evidence I was failing at being human.",
    "I used grounding tricks I read about: five things I could see, four I could touch; it helped until my mind wandered again thirty seconds later.",
    "Feeling 'not enough' showed up when I compared my weekend to other people's highlight posts, even though I know that's a rigged game.",
    "Therapy today dug into patterns I've run since childhood; I left drained but with one phrase stuck in my head that might actually shift something.",
    "I didn't want to pathologize a bad day, but three bad weeks in a row starts to look like a signal, not noise.",
    "Hypervigilance in crowds: scanning exits, shoulders up, exhausted by the time I got home without anything actually happening.",
    "The intrusive thought wasn't even interesting, just loud, and I practiced letting it pass without arguing with it—easier said than done.",
    "Mood lifted slightly after a walk; I refuse to turn that into toxic positivity, but I'll take the inch.",
    "I journaled honestly about shame I've never said out loud; the page held it better than my throat did.",
]

# Energy, motivation, productivity (varied wording)
_SENT_ENERGY = [
    "By mid-morning I had real momentum—tasks were actually leaving the list instead of breeding while I stared at them.",
    "Energy crashed hard after lunch; I leaned on coffee and still moved like I was walking through syrup.",
    "I felt wired and tired at once, that brittle buzz after too little sleep where you get things done but feel fragile doing it.",
    "Productive in the shallow sense: inbox zero, calendar color-coded, soul still asking what any of it was for.",
    "Burnout whispered today, not shouted—resentment toward small asks because my tank already felt empty.",
    "Flow showed up for an hour on a project I care about; I forgot to check the clock until my neck complained.",
    "Motivation wasn't inspiration; it was deciding to start for five minutes and noticing the five minutes kept going.",
    "I measured the day by output and hated that, then still felt a flicker of pride when I shipped the draft.",
    "Rest wasn't lazy—it was strategic. I napped twenty minutes and came back sharper for the evening block.",
    "My body felt light after stretching; mental energy lagged behind, but I'll take mismatched signals over total shutdown.",
]

# Habits, goals, routines: mornings, exercise, creative practice, reading
_SENT_HABITS = [
    "Set the alarm for five-thirty to protect a quiet hour before the world needed me; I actually got up, which still surprises me.",
    "Morning run in the cold: lungs burning, playlist loud, the kind of pain that clears my head better than another hour of scrolling.",
    "Skipped the gym without spiraling; walked thirty minutes at lunch instead and counted that as keeping the promise to move.",
    "Put two hours into a painting I've been avoiding; the canvas is awkward but the brush felt honest.",
    "Read forty pages of a novel without checking my phone—small miracle, tracked it like a goal because I need wins I can see.",
    "Blocked calendar time for deep work like someone I admire would; the block held, mostly, except for one 'urgent' ping I shouldn't have answered.",
    "Meal prep Sunday actually happened: containers lined up like I have my life together, at least in the fridge.",
    "Journaled three gratitudes before bed; felt cheesy, did it anyway because the habit matters more than the vibe.",
    "Signed up for a language app streak; day twelve and the owl's judgment is already getting personal.",
    "Cleaned the desk so I could think; half the 'productivity hack' is removing visual noise.",
    "Practiced guitar until my fingertips hurt; progress is slow but the sound is starting to resemble music.",
    "Tracked water intake like a person who forgets they have a body; hydration helped the afternoon headache more than I wanted to admit.",
]

# Friends, family, travel, outings
_SENT_SOCIAL = [
    "Coffee with an old friend turned into two hours of real talk—travel plans, messy relationships, laughter that loosened my shoulders.",
    "Booked flights for a trip in the fall; the confirmation email made the future feel slightly less abstract.",
    "Weekend away last month still echoes: new streets, bad hotel coffee, good company, the reset I didn't know I needed.",
    "Dinner with coworkers was louder than my comfort zone; I stayed two hours and left proud I didn't flee after one drink.",
    "Video call with family across time zones: lag, repetition, love anyway, and my mom's worry disguised as questions about eating.",
    "Solo museum afternoon: moved slowly, read every plaque, let myself feel small next to big art.",
    "Hosted game night; competitive friends, snacks everywhere, my apartment smelled like popcorn and relief.",
    "Train delay on the way to visit someone mattered less than the hug at the station when I finally arrived.",
    "Tried a new climbing gym with a friend; fear and adrenaline, then pizza that tasted earned.",
    "Farmer's market Saturday: overstimulation in a good way—colors, samples, a bouquet I didn't need but bought.",
]

# General life / work / bridge sentences
_SENT_GENERAL = [
    "Work was a chain of small fires—nothing heroic, just answering emails between meetings and pretending it's sustainable.",
    "The news cycle left me irritable; I closed the tab and went outside, which helped until I checked my phone again.",
    "Weather swung from sun to sudden rain; I got soaked between meetings and laughed because getting mad wouldn't dry my shoes.",
    "Ordinary logistics ate the day: pharmacy, DMV number, a form that asked me to prove I'm still me.",
    "Neighbor's music thumped through the wall; I knocked once, they turned it down, civility still works sometimes.",
    "I cooked something simple and ate it without a screen; tasted more than I expected.",
    "Late night scrolling stole an hour I meant for sleep; classic trade I'm still bad at negotiating.",
    "Commute audiobook: half a chapter, enough ideas to feel slightly smarter stepping off the train.",
    "Budget stress is boring to write about but loud in my head when the card declines at the wrong moment.",
    "Nothing huge happened; I'm logging the day anyway because small days stack into a life.",
]


def _word_count(text: str) -> int:
    return len(text.split())


def _build_entry(rng: random.Random) -> str:
    """
    Assemble 50–200 words by combining sentences from themed pools (mental health, energy,
    habits, social/travel, general). Starts with 4–6 sentences from shuffled themes, then adjusts.
    """
    pools: list[list[str]] = [
        _SENT_MENTAL,
        _SENT_ENERGY,
        _SENT_HABITS,
        _SENT_SOCIAL,
        _SENT_GENERAL,
    ]
    flat = [s for p in pools for s in p]
    used: set[str] = set()
    sentences: list[str] = []

    def _pick_from(pool: list[str]) -> str | None:
        for _ in range(80):
            s = rng.choice(pool)
            if s not in used:
                used.add(s)
                return s
        return None

    # 4–7 sentences, rotating through shuffled themes so each entry mixes topics
    n_start = rng.randint(4, 7)
    order = list(range(len(pools)))
    rng.shuffle(order)
    for k in range(n_start):
        pool = pools[order[k % len(pools)]]
        s = _pick_from(pool)
        if s:
            sentences.append(s)

    text = " ".join(sentences)
    wc = _word_count(text)

    # Sometimes add another sentence to use more of the 50–200 band (if it fits)
    if wc < 165 and rng.random() < 0.5:
        for _ in range(40):
            s = _pick_from(flat)
            if s is None:
                break
            if wc + _word_count(s) <= _MAX_WORDS:
                sentences.append(s)
                text = " ".join(sentences)
                wc = _word_count(text)
                break

    # Grow if under minimum
    tries = 0
    while wc < _MIN_WORDS and tries < 100:
        tries += 1
        s = _pick_from(flat)
        if s is None:
            break
        if wc + _word_count(s) > _MAX_WORDS:
            continue
        sentences.append(s)
        text = " ".join(sentences)
        wc = _word_count(text)

    # Shrink if over maximum (drop from end)
    while wc > _MAX_WORDS and len(sentences) > 1:
        removed = sentences.pop()
        used.discard(removed)
        text = " ".join(sentences)
        wc = _word_count(text)

    if wc > _MAX_WORDS:
        words = text.split()[:_MAX_WORDS]
        text = " ".join(words)
        last_period = text.rfind(". ")
        if last_period > 40:
            text = text[: last_period + 1].strip()
        wc = _word_count(text)

    # If trimming broke minimum, add one short sentence from general pool
    if wc < _MIN_WORDS:
        for s in sorted(_SENT_GENERAL, key=lambda x: _word_count(x)):
            if s in used:
                continue
            if wc + _word_count(s) <= _MAX_WORDS:
                text = f"{text} {s}".strip()
                break

    return text.strip()


def _random_dates(n: int, end: date, rng: random.Random) -> list[date]:
    """N dates uniformly spread across ~365 days ending on `end`, sorted chronologically."""
    # ~12 months back so `end` is inside the drawable range (364-day window excluded last few days)
    start = end - timedelta(days=365)
    span_days = (end - start).days
    out = [start + timedelta(days=rng.randint(0, span_days)) for _ in range(n)]
    out.sort()
    return out


def generate_dataframe(n: int, seed: int, end: date | None) -> pd.DataFrame:
    rng = random.Random(seed)
    end_d = end or date.today()
    dates = _random_dates(n, end_d, rng)
    times = ["morning", "afternoon", "evening"]
    rows = []
    seen_text: set[str] = set()
    for d in dates:
        for attempt in range(30):
            text = _build_entry(rng)
            if text not in seen_text:
                seen_text.add(text)
                break
        else:
            text = _build_entry(rng) + f" ({attempt})"  # extremely unlikely
            seen_text.add(text)
        dow = d.strftime("%A")
        tod = rng.choice(times)
        rows.append({"date": d, "day_of_week": dow, "time_of_day": tod, "text": text})
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df


# 2. CLI #################################


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic journal_entries.csv")
    parser.add_argument("--rows", type=int, default=200, help="Number of entries (default 200)")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="RNG seed for reproducibility")
    parser.add_argument(
        "--end-date",
        type=str,
        default=None,
        help="Last day of the 12-month window (YYYY-MM-DD). Default: today",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default="journal_entries.csv",
        help="Output CSV path (default: journal_entries.csv next to this script)",
    )
    args = parser.parse_args()
    end = date.fromisoformat(args.end_date) if args.end_date else date.today()
    out_path = Path(__file__).resolve().parent / args.output
    df = generate_dataframe(args.rows, args.seed, end)
    df.to_csv(out_path, index=False, date_format="%Y-%m-%d")
    print(f"Wrote {len(df)} rows to {out_path}")
    print(f"Date range: {df['date'].min().date()} .. {df['date'].max().date()}")


if __name__ == "__main__":
    main()
