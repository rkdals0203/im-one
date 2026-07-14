# Database SaaS UI References

## Reference Mix

The redesigned POC UI combines patterns from four database-focused SaaS products:

- Outerbase: AI-assisted database exploration and a plain-language query workflow.
- Xata: saved queries, SQL/results split workspace, and generated SQL experience.
- MotherDuck: notebook-like SQL workbench with database browser and result inspection.
- Neon: compact developer-console feel with query history, saved queries, and runtime/status controls.

## Applied Patterns

- Left rail for persistent product navigation.
- Schema browser with tables and columns on the left.
- Center workbench for natural-language prompt, generated SQL, and result grid.
- Right AI copilot panel for explanation, validation trace, and guardrails.
- Monochrome color system with mint used only for active states, run actions, and validation.

## Color Direction

The UI deliberately avoids multi-accent palettes. The base is black/white/gray, while mint signals action and trust:

- Dark base: `#050505`, `#0B0B0B`, `#111111`
- Light base: `#F7F7F7`, `#FFFFFF`, `#EDEDED`
- Mint accent: `#00C4A8`
- Mint active/soft: `rgba(0, 196, 168, 0.12)`
