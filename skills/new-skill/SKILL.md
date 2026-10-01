---
name: new-skill
description: Write a new skill, or decide whether something belongs in AGENTS.md instead. Trigger when a procedure is being repeated or someone proposes adding a section to the briefing file.
---

# Writing a skill

## The decision first
| The content is... | Goes in |
|---|---|
| True for every task in the repo (layout, commands, hard rules) | AGENTS.md |
| A procedure for a *specific* recurring task | a skill |
| Needed once, for this task only | the conversation |

AGENTS.md is charged on every request. A skill is charged only when it triggers.
When in doubt, skill.

## Shape
```
skills/<name>/SKILL.md
---
name: <kebab-case, matches the directory>
description: <what it does AND when to trigger it. Under 400 chars — this part
             is always-on context. Lead with the trigger condition.>
---
# <Name>
## When this triggers
## Steps            <- numbered, imperative, each independently checkable
## Hard rules       <- what not to do, especially the tempting shortcut
```

## Rules
- The **description** is the only always-on part. It must contain the trigger
  words someone would actually use. A skill nobody triggers is worse than no
  skill: it costs context and delivers nothing.
- The **body** is free — be specific, include the exact commands, show the failure
  modes. Do not compress the body to save tokens; that is not where the cost is.
- One skill per procedure. A skill covering three things triggers for none of them.
- Run `make context` after adding one. If the index grew a lot, your description
  is too long.
