# Plans

How to plan work on ARIA so the next person, or agent, can pick it up from the repository alone.

## Pick the weight

| Change | Plan |
| --- | --- |
| One package, one sitting, no deploy risk | The pull request description: what, why, how verified |
| Several packages, more than a day, or a deploy that changes infrastructure | An execution plan in `docs/exec-plans/active/` |
| A choice others will have to live with | A design doc in [design-docs](design-docs/index.md), linked from the plan |

## Execution plans

Create `docs/exec-plans/active/<short-name>.md` from this outline, and commit it with the first change:

```markdown
# <What will be true when this is done>

Status: active · Started YYYY-MM-DD · Owner: <name>

## Goal
## Scope            (what's in, what's explicitly out)
## Steps            (checkboxes, in order; each one verifiable)
## Verification     (the tests and live checks that prove it)
## Decision log     (date, decision, why; newest last)
## Progress log     (date, what happened; newest last)
```

- Keep it current as you work. A plan that doesn't match reality is worse than none.
- Record decisions when they're made, with the reason. A rejected option is worth one line.
- When it's done, add the outcome, set `Status: completed`, and move the file to `docs/exec-plans/completed/`.
- Gaps you won't fix now go into the [tech-debt tracker](exec-plans/tech-debt-tracker.md), not into a plan.

## What a plan must cover for a deploy

- The reviewed plan you'll deploy from ([release](guides/release.md)), and whether any stack must deploy first ([deployment safety](design-docs/deployment-safety.md)).
- The live checks you'll run on the stage afterwards ([testing](guides/testing.md)).
- How you'd roll back.
