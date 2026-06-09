# Sheldon — Brand Guide

> *"I've drafted an Agreement. It has clauses. You'll thank me."*

## The name

**Sheldon.** Named for the archetype of the brilliant, rules-obsessed roommate who greets every new
cohabitant with a meticulously prepared **Roommate Agreement** — because a shared space only works
when everyone agrees on where things go, how things are done, and what is *non-negotiable*.

Your codebase just got new roommates: **AI agents**. Sheldon is the Agreement — and the stickler who
makes sure the apartment is in a state where any roommate, human or agent, can thrive without burning
the place down.

- **Command:** `sheldon`
- **Package (PyPI):** `sheldon-readiness` · **npm:** `sheldonhq` · **GitHub:** `getsheldon/sheldon`
- **Domain:** `sheldon.tools`

## The one-liner

**The Roommate Agreement for your codebase — so your agents and your team play by the same rules.**

Sheldon reads your repo, assigns it a readiness **Level (1–5)**, cites the evidence for every clause,
and drafts the **amendments** to get you to the next level. Deterministic. Non-negotiable.
Occasionally smug.

## Positioning

Sheldon doesn't write your features — he makes the apartment *liveable for agents*. He's not a linter,
not a SaaS dashboard, not a vibe. He's the one who notices the AGENTS.md is missing, the CI doesn't
actually run the tests, and *someone* committed without a CODEOWNERS, and who hands you a numbered list
to fix it. The deterministic engine is the Agreement; the agent layer is Sheldon explaining it to you
(whether you asked or not).

**Against the field:** file-existence tools are roommates who *say* they'll do the dishes. Factory is a
landlord who keeps the rules in a building you don't own. Sheldon lives with you, shows his work, and
the Agreement is yours.

## Voice & tone

Precise, pedantic, supremely confident, secretly kind. Speaks in **clauses and sections**. Corrects
you — then tells you *exactly* how to be right. Never cruel; merely *certain*.

| Do | Don't |
|---|---|
| "Section 3.7: thou shalt ship an AGENTS.md." | quote any TV show, verbatim lines, or catchphrases that function as trademarks |
| "Your repo is on **Rung 2**. This is non-negotiable until it isn't." | be mean — Sheldon is exasperated, not hostile |
| triple-knock cadence: *"knock knock knock. Your CI."* | let the bit get in the way of a clear error message |
| flowcharts, brief footnotes, "I've prepared a brief." | use the full character name, series title, or any likeness/logo |

**Rule of thumb:** delight lives in headers, the CLI banner, and marketing. **Instructions, errors, and
report bodies stay plain and useful** — Sheldon is pedantic about clarity too.

### Sanctioned lines (all original)
- "The Agreement is ready for your signature."
- "I'm not judging your repo. I'm *grading* it. There's a difference, and it's on a five-point scale."
- "knock knock knock. Your readiness. knock knock knock. Your readiness."
- "I have prepared 11 amendments. We'll start with the ones that won't hurt."
- "That's my spot — and this is your `.gitignore`. Both are sacred."

## Feature naming (in-voice → function)

| In-voice | What it is | Command |
|---|---|---|
| **The Agreement** | the readiness report (Level + cited criteria) | `sheldon report` |
| **The Amendments** | safe remediation scaffolds + drafts | `sheldon fix` |
| **Rungs** | maturity Levels 1–5 (climb them) | shown in the report |
| **Clauses** | individual criteria | the per-pillar lines |
| **Negotiated exceptions** | waivers (Sheldon disapproves, but allows) | `.agents/readiness/waivers.json` |
| **House rules at the door** | the CI gate (`--min-level`) | `sheldon`/CI Action |

## Visual identity

### The mark — an evoked silhouette (no identifying marks)
A flat, faceless silhouette of a **tall, slim, very upright figure**, **one index finger raised** in
gentle correction, wearing the signature **layered tee** (short sleeve over long sleeve) with a
**blank superhero roundel** on the chest. Recognition comes entirely from **archetype + context**, never
from a likeness:

- **Pose:** ramrod-straight posture; the raised "well, actually" finger (or arms crossed, alt).
- **Wardrobe:** the double-layered tee + blank roundel emblem.
- **Props (banner only):** a couch with one cushion stamped **"MY SPOT"**, and a **"knock, knock, knock."**
  speech bubble. The props make it unmistakable while the figure stays generic.

**Hard guardrails (this is the whole point):** no face, no real superhero logos (blank roundel or `§`
only), no actor likeness, no character full name, no series name, no trademarked catchphrases. It is an
*original illustration of an archetype.*

### Color

| Token | Hex | Use |
|---|---|---|
| **Ink** | `#14142B` | primary; the "legal document" seriousness; the silhouette |
| **Agreement Gold** | `#E8B23A` | accent; clauses, the wax-seal stamp, Level badge |
| **Cleared** | `#2FBF71` | pass |
| **Breach** | `#E5484D` | fail |
| **Pending** | `#E8B23A` | unknown / waived |
| **Paper** | `#F7F5EF` | document background |
| **Slate** | `#5B5B73` | secondary text |

### Typography
- **Wordmark / headings:** a precise geometric grotesk — **Space Grotesk** (fallback: Archivo, system sans).
- **Body / "the legal text":** a monospace — **JetBrains Mono** (fallback: IBM Plex Mono, ui-monospace).

### Motifs
Ben-Day comic dots (Ink at ~8% opacity), the **§** section mark, a flowchart arrow, and a stamped seal:
**`CERTIFIED READY · LEVEL N`**.

## Trademark note

"Sheldon" is used as a product mark for a developer tool. We deliberately avoid the full character name,
the series title, the actor's likeness, and any catchphrase that functions as a trademark. All copy is
original; the silhouette is an original archetype illustration with no identifying marks. If a conflict
ever surfaces, the `sheldonhq` / `sheldon-readiness` namespaces and `sheldon.tools` give us room to adjust.
