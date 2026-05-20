# docs/scratch/

**Everything else in this directory is gitignored.** This README is the only file that travels with the repo.

## Purpose

Agent and human thinking-out-loud lives here: exploratory notes, intermediate analyses, design sketches that didn't make it into the spec, Ralph-loop review outputs, brain dumps. Cheaper to write than to defend.

## Rules

- **Default-scratch.** If you're not sure whether a doc is durable, put it here.
- **Promote, don't move.** When a scratch note becomes durable, recreate the content under `docs/` with structure and intent — don't `git mv` and re-edit. The scratch → durable transition is a *decision*, not a rename.
- **Don't reference scratch from durable docs.** Specs, plans, `CLAUDE.md`, `README.md` must not link into `docs/scratch/` — those links would 404 for anyone cloning fresh.
- **Ralph review outputs** can land here as `.review-<slice>.md`. The *disposition* (fixed / deferred / rejected) gets distilled into the slice's row in `docs/build-progress.md`; the raw review can be discarded after.
- **Periodically delete.** When a slice closes, prune scratch files that fed into it.

## What goes in `docs/` instead

Anything that defines a contract, invariant, deliverable, or is referenced by other durable artifacts. See the decision tree in `CLAUDE.md` § "Documentation: durable vs transient".
