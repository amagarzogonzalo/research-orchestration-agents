---
name: ship
description: Prepare completed work for human review. Trigger when the task is done and `make check` is green.
---

# Ship

You never commit and never push. You prepare, then hand over.

1. `make check` — fully green. Do not proceed on a partial pass.
2. `git diff` — read your own diff end to end. Delete debug prints, stray
   comments, and anything added "just in case".
3. Run `@critic` on the diff. Report its findings verbatim, then state which you
   agree with. Disagreeing is fine; silently ignoring is not.
4. `git add -p` the intended files only. Never `git add -A`.
5. Write the handover, exactly this shape:

```
CHANGE:    <one sentence, behavior not implementation>
COVERED BY: <test/eval file, or UNCOVERED + why that's acceptable>
CONTEXT:   <unchanged | always-on budget moved from X to Y because ...>
RISK:      <what breaks if this is wrong, and how you'd notice>
NOT DONE:  <what a reviewer might expect that you deliberately skipped>
```

6. Stop. The human commits.

`NOT DONE` is the important line. An empty one usually means the author didn't look.
