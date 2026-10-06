# AGENTS.md

Working agreement for agents on this repo. Frontend-specific rules live in
`webapp/AGENTS.md` and are not duplicated here.

## Sibling repos (outside this tree)

| What | Path |
| --- | --- |
| mpv source | `/home/arman/Documents/project/w2g/mpv` |
| mpv-android | `/home/arman/Documents/project/w2g/mpv-android` |

These are separate repositories. Reference them in place — never vendor,
copy, or fork their source into this tree, and never edit them from a branch
of this repo. Design context: `docs/adr/0002-mpv-separate-process.md`.

## Rule 2' — vertical slices, not big-bang refactors

Never reorganize code you are not touching for a feature in the current
phase. When a phase touches a domain of `app.py`, extract exactly that
domain into its module **in the same phase**:

1. characterization tests first, pinning current behaviour
2. then move the code
3. then **delete** the old code from `app.py` in the same branch

Untouched domains stay in `app.py` until their own phase. No speculative
modules, no empty packages, no "v2" copies.

## Rule 5' — layering, in every module created or extracted

```
handlers (thin)  →  services (no Flask/SocketIO imports)  →  adapters (DB, filesystem, ffmpeg/ffprobe, network)
```

Dependencies point downward only. Enforced by an import-boundary test that
is grown slice by slice — as each slice lands, not written up front.

## Deferred on purpose

`BACKEND_RULES.md` is drafted at the **end of phase 1** from what was actually
done, and finalized in phase 4. Do not create it now, and do not write rules
for modules that do not exist yet.

Current state for reference: `app.py` is ~4156 lines; `media_pipeline/`
already holds `errors.py`, `ffmpeg_cmd.py`, `planner.py`, `probe.py`,
`runner.py`. No import-boundary test exists yet — rule 5' expects it to
appear alongside the next slice, not ahead of it.
