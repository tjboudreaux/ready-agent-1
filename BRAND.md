# Ready Agent 1 — Brand Guide

> **READY?**  Player One has logged in. Is your codebase ready for the agents?

## The name

**Ready Agent 1.** A nod to the arcade-quest spirit of *being ready before you enter the world* — except
the world is your codebase and the players are AI agents. The first agent to log in either thrives or
rage-quits. Ready Agent 1 tells you which, and helps you **clear all five gates** to get there.

- **Command:** `ra1`
- **Skills:** `ra1-report`, `ra1-fix`
- **Package (PyPI/npm):** `ra1` · **GitHub:** `ready-agent-1` · **Domain:** `ra1.sh`

## The one-liner

**Is your codebase ready for the agents? Score it, clear the gates, level up.**

Ready Agent 1 scans your repo, assigns a readiness **Level (1–5)** — five gates to clear — cites the
evidence for every check, and hands you the **loadout** to reach the next level. Deterministic. Reproducible.
No continues required.

## Positioning

The agent era booted up and your repo is the world they have to play in. Ready Agent 1 is the
pre-game readiness check: does the world have the docs, tests, guardrails, and signage an agent needs to
*win* — or will it spawn into chaos? It doesn't play the game for you (it won't write your features); it
makes sure the level is beatable.

**Against the field:** file-existence tools check that the cartridge exists. Factory is an arcade you don't
own a key to. Ready Agent 1 runs on your machine, shows the score on every check, and the save file is yours.

## Voice & tone

Arcade hype meets a good co-op teammate. Encouraging, fast, a little 1986. Talks in **gates, levels,
loadouts, and high scores** — never condescending, always "here's how you clear it."

| Do | Don't |
|---|---|
| "**Gate 3 cleared.** Two to go." | reference *Ready Player One* by name, its characters, the OASIS, or its key art/logo/font |
| "Player One is ready. Your repo is on **Level 2** — let's level up." | overclaim — the score is deterministic, not vibes |
| "Loadout ready: 9 power-ups staged. Press **fix --apply** to gear up." | let the arcade bit bury a clear error message |
| "Insert AGENTS.md to continue." | use any trademarked tagline; write original lines |

**Rule of thumb:** neon and hype live in the banner, headers, and marketing. **Errors, reports, and
instructions stay plain and useful.**

### Sanctioned lines (all original)
- "READY? Your codebase isn't. Yet."
- "Level 3 of 5 cleared. The next gate needs branch protection."
- "New high score: 22/24 checks. GG."
- "Insert an AGENTS.md to continue."
- "Player One has entered the repo. Brace for impact."

## Feature naming (in-voice → function)

| In-voice | What it is | Command |
|---|---|---|
| **Readiness Scan** | the report (Level + cited checks) | `ra1 report` |
| **The Loadout** | safe remediation scaffolds + drafts | `ra1 fix` |
| **Gates / Levels** | maturity Levels 1–5 (clear them to advance) | shown in the scan |
| **Checks** | individual criteria | the per-pillar lines |
| **Overrides** | waivers (skip a gate on purpose) | `.agents/readiness/waivers.json` |
| **Clear-to-merge** | the CI gate (`--min-level`) | `ra1` / CI Action |

## Visual identity

Full **synthwave / 80s-retro-future**: neon on midnight, perspective grid, a striped synth-sun on the
horizon, scanlines, and an arcade level-select.

### The mark
A neon badge — a chrome **"1"** (or **RA·1** monogram) inside a hex/roundel, ringed in cyan→magenta glow
over a faint grid. Reads as a player number / droid designation. No likeness, no logo lift.

### Color (synthwave)

| Token | Hex | Use |
|---|---|---|
| **Midnight** | `#0B0A1E` | background / void |
| **Deep Violet** | `#2A0A4A` | gradient mid / panels |
| **Neon Magenta** | `#FF3CAC` | primary neon; wordmark, fail |
| **Neon Cyan** | `#2DE2E6` | secondary neon; grid, links, pass |
| **Electric Purple** | `#7A04EB` | accents, glow |
| **Sun Amber** | `#FFD36E` | synth-sun top, highlights |
| **Hologram** | `#EAF6FF` | near-white text on dark |

Gradients: sky `#0B0A1E → #2A0A4A → #FF3CAC` (toward horizon); sun `#FFD36E → #FF3CAC`.

### Typography
- **Wordmark / display:** a wide techno face — **Orbitron** (fallbacks: Eurostile, "Arial Black", sans-serif).
- **Body / HUD:** monospace — **Share Tech Mono** (fallbacks: JetBrains Mono, ui-monospace).

### Motifs
Perspective neon grid (vanishing-point floor), the slitted synth-sun, CRT scanlines (2px lines ~6% white),
a level-select row `1 ▸ 2 ▸ 3 ▸ 4 ▸ 5` with cleared gates lit, and a blinking **`▮ PRESS START`** / **`INSERT COIN`**.

## Trademark note

"Ready Agent 1" is an original product name that *alludes* to a well-known arcade-quest title as a genre
wink. We deliberately use **no** trademarked title text, character names, story elements (e.g. the OASIS),
logo, key art, or the film/book's typography — only generic synthwave/retro-future visual language and
original copy. The `ra1` / `ready-agent-1` namespaces and `ra1.sh` keep us clear and flexible.
