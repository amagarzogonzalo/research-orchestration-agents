# Context efficiency

Everything the model reads is paid for, on every turn, and long context makes
instruction-following worse — not just pricier. Treat context as a budget.

## The four levers, cheapest first

1. **Progressive disclosure.** AGENTS.md is a map. Procedures live in
   `skills/*/SKILL.md` and cost nothing until triggered; only the skill's
   name + description are always-on. `make context` shows the split.

2. **Retrieval over stuffing.** A million-token window is a ceiling, not a
   target. Sending a whole repo measurably degrades finding the relevant 2k.
   Grep, read the file you need, stop.

3. **Cache-prefix discipline.** Most providers cache on the prompt *prefix*, and
   a hit is far cheaper than a miss. Order every request **static first,
   volatile last**:

       [system] -> [AGENTS.md] -> [rules] -> [stable file context] -> [the task]

   Anything injecting a timestamp, random id, or reshuffled file list near the
   top invalidates the cache on every request. If your cache hit rate is near
   zero, look there first.

4. **Tool-output compression** (optional; off by default). Tool output, logs and
   file dumps are the bulk of a long coding session and compress far better than
   prose. A local compression proxy sits between the agent and the provider:

       make compress-on    # start it and route opencode through it
       make compress-off

   Before enabling, know three things: telemetry may be on by default in these
   tools (turn it off), some install extra MCP servers at *user* scope so they
   leak outside this project, and the headline compression numbers are
   workload-dependent — prose barely compresses, repetitive JSON compresses
   enormously. Measure on your own traffic, then decide. See `make compress-help`.

## MCP is not free
Every connected server's tool schemas are injected every turn, including turns
that will never call them. Three servers is usually too many. The budget in
`harness.toml` is deliberately tight; if you need many tools, the answer is a
tool-retrieval layer, not a bigger budget.

## When context is genuinely too big
Split the task. A task needing 200k tokens of context is usually two tasks with
a written artifact between them. Write the intermediate result to a file and
start fresh — that also survives a crash, which a long session does not.
