# Bruno-tama Roadmap

This document outlines the planned evolution of Bruno, categorized by the "depth" of the feature.

## 🟢 Phase 1: Persistence & Lifecycle
*Goal: Make Bruno feel like a long-term companion rather than a transient process.*

- [ ] **State Persistence:** Save hunger, energy, mood, and age to `~/.local/share/bruno/state.json`.
- [ ] **Aging System:**
    - Day 0-7: "Baby" (Current forms).
    - Day 8-30: "Adult" (New sprites, maybe a tiny hat).
    - Day 30+: "Elder" (Different idle patterns, slower movement).
- [ ] **Holiday Spirits:** Date-aware sprite modifications (Santa hat, Pumpkin, Birthday cake).

## 🟡 Phase 2: Context Awareness
*Goal: Integrate Bruno into the developer's workflow.*

- [ ] **Git Integration:**
    - Happy reaction to `git commit`.
    - Waving/Pushing animation for `git push`.
    - "New branch" bubble for `git checkout -b`.
- [ ] **Build Monitor:**
    - Concern/Hug animation if the last shell command failed (`$? != 0`).
    - "Ding!" or "Done!" bubble for long-running commands (>30s) finishing.
- [ ] **File-type Affinity:** React to the extension of edited/viewed files (e.g., "Python! 🐍" or "Safe Rust! 🦀").

## 🟠 Phase 3: Visual Polish & Particles
*Goal: Add "juice" to the ASCII rendering.*

- [ ] **Particle System:**
    - `zZz` floating particles during SLEEP.
    - `<3` or `*` sparks when petted/fed.
    - Dust puffs when landing from a "teleport".
- [ ] **Mood Auras:** Very dim/subtle SGR background colors or "shadow" characters under Bruno to give 2.5D depth.

## 🔴 Phase 4: Shell Integration ("The Magic Words")
*Goal: Interaction without keyboard shortcuts.*

- [ ] **Stat Command:** `bruno:stats` in the shell shows a temporary status bubble.
- [ ] **Inter-Process Feeding:** `echo "🍎" > /tmp/bruno_feed` triggers a feed animation in the active shell.
- [ ] **Stealth Mode:** `bruno:hide` / `bruno:show` commands to toggle visibility via shell interaction.

## 🔵 Phase 5: Multi-Pet Ecology
*Goal: Let Brunos in different panes recognize each other.*

- [ ] **Shared Lockfile:** Track positions of all active Brunos in a global state file.
- [ ] **Proximity Awareness:** If two Brunos are "near" each other (e.g., same CWD in different panes), they wave or acknowledge the sibling.
