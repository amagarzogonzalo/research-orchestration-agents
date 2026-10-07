

Yes — and after looking at the **current repo README/architecture**, I can answer these more precisely. The key correction is:

> **It is not “event-driven because it uses LangGraph.”**  
> It is a **stateful graph workflow**; LangGraph provides the execution model, including parallel node execution and checkpointing. The repo itself layers recursive agents, budgets, search tools, and a durable writer on top. [GitHub](https://github.com/boluodaixue/paperpilot)

## 1. How exactly does it fork subagents?

The clever bit is that it **doesn't have a separate “SubAgent” implementation**.

The repo says:

> root Agent, child Agent, and grandchild Agent all run the **same AgentGraph**, differing only in identity, depth, and budget. [github.com](https://github.com/boluodaixue/paperpilot)

So conceptually:

```text
AgentGraph(question=A)
       |
       | decides: "I need independent research"
       |
       +----> AgentGraph(question=A1, depth=1, budget=...)
       |
       +----> AgentGraph(question=A2, depth=1, budget=...)
       |
       +----> AgentGraph(question=A3, depth=1, budget=...)
                         |
                         +----> AgentGraph(question=A3a, depth=2, ...)
```

That's **recursive graph composition**.

The important consequence is that the child doesn't get a dumb prompt like:

```text
"You are a researcher. Go search X."
```

It gets essentially the **same research machinery**:

```text
plan / reason
    ↓
choose tool
    ↓
collect evidence
    ↓
evaluate gap
    ↓
continue / stop / fork
```

but with:

```text
depth = parent_depth + 1
budget = smaller remaining budget
identity = child task
```

The README explicitly says the whole execution tree shares recursion, thread, tool and time budgets. [github.com](https://github.com/boluodaixue/paperpilot)

That is a very nice design because you don't need to invent:

```python
ResearchAgent
PaperAgent
WebAgent
CriticAgent
SubResearchAgent
```

You can instead have:

```python
ResearchAgentGraph(...)
```

and instantiate it recursively.

---

# 2. How can many agents run concurrently?

This is where **LangGraph's execution model** matters.

LangGraph uses a graph/state execution model derived from the Pregel/BSP idea: nodes process state, write updates to channels, and multiple ready nodes can execute in parallel during an execution step. [GitHub](https://github.com/langchain-ai/langgraph/blob/main/.github/THREAT_MODEL.md?utm_source=chatgpt.com)

So conceptually:

```text
                  Parent
                    |
          decides to fork 3 tasks
                    |
          ┌─────────┼─────────┐
          ↓         ↓         ↓
       Child A   Child B   Child C
          │         │         │
       search     search     search
          │         │         │
       evidence   evidence   evidence
          └─────────┼─────────┘
                    ↓
                 merge
```

The children aren't necessarily "three Python processes."

Think more in terms of:

> **three independent graph branches that become runnable and are scheduled by the graph runtime.**

And that distinction matters.

### Forking is logical concurrency

The graph says:

```text
I need A, B and C.
```

### Actual parallelism is runtime-dependent

Whether those execute truly simultaneously depends on the runtime/tool implementations, async behavior, API latency, rate limits, etc.

So don't equate:

```text
3 graph branches
```

with:

```text
3 OS processes
```

or even automatically:

```text
3 simultaneous HTTP requests
```

The graph **permits/represents concurrent work**; the underlying node/tool implementation determines the actual I/O concurrency.

---

# 3. What does SQLite actually do?

There are **two quite different SQLite ideas** in this repo.

This is important.

## A. SQLite as the LangGraph checkpointer

The architecture explicitly says:

```text
LangGraph Research Workflow
          ↓
   SQLite Checkpointer
``` :chatgpt-content-reference{index="4"}


This is about **workflow state**, not your knowledge base.

A checkpoint can conceptually look like:

```text
thread_id = research_123

checkpoint:
    current_node = research
    depth = 2
    pending_tasks = [...]
    accumulated_evidence = [...]
    tool_state = [...]
    interrupt_state = ...
```

When the application stops/crashes/restarts:

```text
SQLite
   ↓
load checkpoint
   ↓
resume graph
```

The repo explicitly says LangGraph checkpoints persist research stages and interrupts, allowing the workflow to resume after a service restart. [github.com](https://github.com/boluodaixue/paperpilot)

LangGraph's official SQLite checkpointer exists specifically to persist graph state and supports synchronous and asynchronous operation. [GitHub](https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint-sqlite/README.md?utm_source=chatgpt.com)

---

## B. SQLite FTS5 as the knowledge retrieval index

This is a different thing:

```text
Markdown Vault
       ↓
   SQLite FTS5
       ↓
 lexical retrieval
```

The README explicitly says:

> SQLite retrieval data is **just a rebuildable index** and cannot overwrite Markdown. [github.com](https://github.com/boluodaixue/paperpilot)

And the repo combines:

```text
FTS5
+
optional semantic retrieval
+
WikiLink neighbors
```

for hybrid retrieval. [github.com](https://github.com/boluodaixue/paperpilot)

So there are conceptually two SQLite roles:

```text
              SQLite
             /      \
            /        \
       Checkpoint     FTS5
           |            |
     workflow state   search index
```

They're solving completely different problems.

---

# 4. Is SQLite actually needed?

### For the research algorithm itself: **No.**

You can build recursive research without SQLite.

For example:

```python
state = {
    "question": ...,
    "evidence": ...,
    "tasks": ...,
}
```

and just keep it in memory.

The research algorithm can still work.

### For robust long-running execution: **very useful.**

Without checkpointing:

```text
researching for 15 minutes
       ↓
process crashes
       ↓
everything disappears
       ↓
start over
```

With checkpointing:

```text
researching
   ↓
checkpoint
   ↓
crash
   ↓
restart
   ↓
resume
```

That is why the repo uses SQLite.

The same idea is used in LangGraph's own checkpoint package. [GitHub](https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint-sqlite/README.md?utm_source=chatgpt.com)

### Is SQLite fundamentally required?

No.

You could substitute:

```text
Postgres
Redis
another checkpoint backend
```

The architectural requirement is:

> **durable state**, not specifically SQLite.

The repository chose SQLite because this is a **local/personal research system**, where SQLite gives persistence without requiring a separate database server. The README explicitly frames SQLite as a rebuildable/lightweight persistence layer. [github.com](https://github.com/boluodaixue/paperpilot)

---

# 5. Is the system event-driven?

### Not in the usual sense.

I'd call it:

> **graph-driven + stateful + checkpointed + partially concurrent**

rather than:

> event-driven architecture.

An event-driven architecture normally looks more like:

```text
Event
  ↓
Message bus
  ↓
consumer
  ↓
event
  ↓
another consumer
```

For example:

```text
ResearchTaskCreated
      ↓
queue
      ↓
Searcher
      ↓
EvidenceFound
      ↓
Critic
      ↓
GapDetected
      ↓
ResearchTaskCreated
```

PaperPilot isn't primarily structured around a message bus like Kafka/RabbitMQ.

Instead it's closer to:

```text
Graph state
    ↓
node executes
    ↓
state updates
    ↓
graph determines next runnable nodes
    ↓
checkpoint
    ↓
continue
```

LangGraph itself describes its execution as a graph/state model in which nodes communicate through channels and execute in supersteps. [GitHub](https://github.com/langchain-ai/langgraph/blob/main/.github/THREAT_MODEL.md?utm_source=chatgpt.com)

So:

```text
LangGraph ≠ event bus
```

Although the graph naturally has **event-like state transitions**.

---

# 6. What web and paper sources does it actually search?

The current README is quite explicit.

### Web

Fallback chain:

```text
Tavily
   ↓
秘塔
   ↓
Exa
   ↓
博查
   ↓
SerpAPI
```

Only configured providers are used. [github.com](https://github.com/boluodaixue/paperpilot)

This means the agent doesn't necessarily hit all five every time.

It generally follows:

```text
preferred provider
      ↓
failure?
      ↓ yes
fallback
```

---

### Papers

The paper retrieval layer can fall back among:

```text
arXiv
Semantic Scholar
OpenAlex
```

The repo also has special handling so a raw arXiv ID is treated as an arXiv identifier rather than incorrectly being interpreted as an OpenAlex ID. [github.com](https://github.com/boluodaixue/paperpilot)

So:

```text
                    paper query
                        ↓
               configured backend
                        ↓
              result good enough?
                  /          \
                yes           no
                 |             |
              return      next provider
```

---

# 7. Does every search result become evidence?

No — and this is important.

The architecture is:

```text
search result
    ↓
inspect/read
    ↓
extract evidence
    ↓
evidence object
    ↓
report
```

not:

```text
Google result = truth
```

The repo explicitly separates:

```text
Raw Source
Evidence / Report
Wiki
```

and says evidence is source-locatable/atomic. [github.com](https://github.com/boluodaixue/paperpilot)

That gives you:

```text
claim
 ↓
Evidence
 ↓
source
 ↓
specific location
```

rather than simply:

```text
claim → URL
```

---

# 8. How does it decide what to search next?

This is basically the heart of the system.

Suppose:

```text
Question:
"How do current LLM memory systems work?"
```

The first agent may discover:

```text
Area A: architecture
Area B: evaluation
Area C: limitations
```

Then after searching A:

```text
Architecture evidence: sufficient

Evaluation:
missing comparisons

Limitations:
weak evidence
```

The system can therefore do:

```text
             Research state
                   ↓
             evidence gaps
             /           \
        architecture    evaluation
             |              |
           done          investigate
```

The repo specifically says the agent chooses between:

```text
continue calling tools
stop
fork
```

based on evidence gaps, while the overall tree shares budgets. [github.com](https://github.com/boluodaixue/paperpilot)

This is much closer to **adaptive planning** than a fixed search plan.

---

# 9. How many agents can it run concurrently?

There are three separate limits you should think about.

### Graph-level concurrency

Multiple branches can be active.

```text
A
B   ← simultaneously runnable
C
```

### API-level concurrency

Suppose three branches each call Tavily:

```text
A → Tavily
B → Tavily
C → Tavily
```

Actual simultaneous HTTP calls depend on the implementation and async execution.

### Budget-level concurrency

The important repo-specific safety mechanism is that the whole research tree shares:

```text
recursion budget
thread budget
tool budget
time budget
```

So the system is trying to prevent:

```text
                  root
              / / / | \ \ \
            50 children
          / / / / / / / / \
       100 grandchildren...
```

from turning into an uncontrolled explosion.

The README makes that shared-budget design explicit. [github.com](https://github.com/boluodaixue/paperpilot)

---

# 10. The really interesting part: this is closer to a search tree than a team of agents

I would mentally model it like this:

```text
                     Research
                        │
              ┌─────────┴─────────┐
              ↓                   ↓
        Search problem 1     Search problem 2
              │                   │
         ┌────┴────┐          ┌───┴────┐
         ↓         ↓          ↓        ↓
      source A  source B   source C  source D
         │         │          │        │
         └────┬────┘          └──┬─────┘
              ↓                   ↓
             evidence            evidence
                  \              /
                   \            /
                    ↓          ↓
                      synthesis
```

That resembles **dynamic tree search** much more than "five personalities chatting with each other."

That's why I like the architecture.

---

# 11. Which files should you read first?

The current repository layout gives the most useful reading order. [github.com](https://github.com/boluodaixue/paperpilot)

I would read it in this exact order:

### 1. `src/research/`

This is the most important directory.

You want to understand:

```text
Workflow
AgentGraph
Memory
Wiki
Writer
Retrieval
```

The README explicitly says those pieces live under `src/research/`. [github.com](https://github.com/boluodaixue/paperpilot)

**Your first mission here is to find the code that constructs the AgentGraph and the node/route that decides `continue / stop / fork`.**

That is the beating heart.

---

### 2. `src/tools/`

Then inspect:

```text
src/tools/
```

The repo says this contains:

```text
search
paper
web
file
compute
```

tools. [github.com](https://github.com/boluodaixue/paperpilot)

You want to see:

```text
LLM decides tool
        ↓
tool invocation
        ↓
external API
        ↓
normalized result
        ↓
research state
```

---

### 3. `docs/`

The README explicitly points at `docs/` for the current architecture and roadmap. [github.com](https://github.com/boluodaixue/paperpilot)

I would read the architecture document **before** reading every implementation file.

It will save you a lot of time.

---

### 4. `tests/`

This is underrated.

The README says tests cover:

```text
research flow
budgets
failure recovery
knowledge read/write
retrieval
web/CLI entry points
fault injection
``` :chatgpt-content-reference{index="21"}


If you want to understand how the author **expects the system to behave**, tests can be more revealing than the implementation.

Look especially for tests around:

```text
fork
recursion
budget
checkpoint
resume
writer
retrieval
```

---

### 5. `scripts/run_single.py`

This is useful to understand:

```text
How does a user's query enter the research engine?
```

The README gives it as the CLI entrypoint for running a research question. [github.com](https://github.com/boluodaixue/paperpilot)

---

### 6. `web/`

Only after you understand the backend.

That's mostly useful for:

```text
HTTP
SSE
UI
interrupt/resume
progress
```

The architecture identifies FastAPI + SSE here. [github.com](https://github.com/boluodaixue/paperpilot)

---

# 12. The five files/concepts I'd specifically hunt for

When you're browsing the repository, don't just search filenames.

Search for these **symbols/concepts**:

```text
StateGraph
compile
checkpointer
Send
Command
fork
recursion
depth
budget
interrupt
```

Especially:

```text
Send
```

because in LangGraph-style implementations, dynamically creating multiple destinations/tasks is one common way to express fan-out.

Also search:

```text
gather
asyncio.gather
create_task
Semaphore
```

because that's where you'll discover whether **actual external calls** are being made concurrently rather than just represented as graph branches.

---

# 13. Other research methods from papers that are worth stealing

This is where it gets really interesting.

I searched the current literature as well, and there are several methods I'd consider better than blindly copying PaperPilot.

## A. STORM — multiple perspectives before writing

STORM's core idea is roughly:

```text
topic
 ↓
generate different perspectives
 ↓
ask targeted questions
 ↓
research sources
 ↓
synthesize structured article
```

Instead of one researcher asking:

> “What do I need to know?”

you intentionally create **multiple viewpoints**.

This is particularly useful for avoiding a single-agent tunnel vision.

The broader research-agent literature still identifies STORM-style multi-perspective planning as an important architecture. [arXiv](https://arxiv.org/abs/2506.18096?utm_source=chatgpt.com)

---

# 14. IRCoT — retrieve *because of* the reasoning

This one is especially interesting.

IRCoT = **Interleaving Retrieval with Chain-of-Thought**.

The key insight is:

```text
reason
 ↓
new knowledge need
 ↓
retrieve
 ↓
new evidence
 ↓
continue reasoning
 ↓
retrieve again
```

instead of:

```text
retrieve everything first
 ↓
reason afterward
```

The authors found that retrieval quality and downstream QA improved substantially on multi-hop tasks, with reductions in hallucination. [arXiv](https://arxiv.org/abs/2212.10509?utm_source=chatgpt.com)

This is philosophically very close to what PaperPilot wants.

I'd actually consider **IRCoT-style retrieval control** a very useful enhancement to this architecture.

---

# 15. WebGPT — browse like a human, retain references

WebGPT introduced an important pattern:

```text
search
 ↓
navigate
 ↓
inspect page
 ↓
follow links
 ↓
collect references
 ↓
answer
```

rather than treating search results as the final source.

The model was trained to browse a text-based web environment and required references to support answers. [arXiv](https://arxiv.org/abs/2112.09332?utm_source=chatgpt.com)

That's useful because it suggests a hierarchy:

```text
Search result
      ↓
source page
      ↓
relevant passage
      ↓
evidence
```

which is stronger than:

```text
search snippet → LLM summary
```

---

# 16. Newer 2025–2026 work: recursive + rubric-driven research

A particularly interesting recent example is **DuMate-DeepResearch**.

It explicitly combines:

```text
dynamic planning
+
reflection
+
re-planning
+
backtracking
+
parallel branching
+
recursive search agents
+
rubric-driven stopping
```

Its architecture separates an agent core for planning/scheduling from a retrieval/tool ecosystem. It then recursively gives complex subtasks to inner search agents. [arXiv](https://arxiv.org/abs/2606.07299?utm_source=chatgpt.com)

That is extremely relevant to your question because it's almost the same problem you're investigating:

```text
How do I decide:
  - what to search?
  - when to branch?
  - when to backtrack?
  - when to stop?
```

Their answer is essentially:

```text
dynamic graph
+
recursive execution
+
explicit quality rubric
```

That's one of the approaches I'd investigate most deeply.

---

# 17. Another important direction: don't let the agent decide "done" by intuition alone

A lot of research systems eventually do:

```text
LLM:
"I think I have enough information."
```

That's weak.

A stronger approach is:

```text
Research rubric
├── coverage
├── source quality
├── source diversity
├── contradictory evidence
├── key claims supported?
└── unanswered questions
```

Then:

```text
score < threshold
    ↓
more research

score ≥ threshold
    ↓
write report
```

DuMate's rubric-based test-time optimization is a concrete recent example of this idea. [arXiv](https://arxiv.org/abs/2606.07299?utm_source=chatgpt.com)

---

# 18. Yet another idea: research as a budgeted search problem

The 2025 deep-research survey explicitly frames modern systems around:

```text
dynamic reasoning
adaptive long-horizon planning
multi-hop retrieval
iterative tool use
structured reports
```

and notes **sequential execution inefficiency** as a major limitation. [arXiv](https://arxiv.org/abs/2506.18096?utm_source=chatgpt.com)

That suggests a useful optimization:

```text
Expected information gain / cost
```

for deciding what branch to explore.

For example:

```text
Task A:
expected evidence gain = 0.8
cost = 2

Task B:
expected evidence gain = 0.4
cost = 1

Task C:
expected evidence gain = 0.9
cost = 10
```

Choose roughly:

```text
gain / cost
```

rather than simply:

```text
highest apparent importance
```

That can dramatically reduce wasted searches.

---

# 19. One more thing I'd borrow: frozen-web evaluation

**Deep Research Bench** makes an important observation: evaluating live-web research agents is difficult because the web keeps changing. Their RetroSearch environment freezes scraped pages so systems can be evaluated reproducibly. [arXiv](https://arxiv.org/abs/2506.06287?utm_source=chatgpt.com)

That is extremely useful if you're building your own system.

Instead of:

```text
run agent today
run agent next month
compare scores
```

you can have:

```text
fixed research corpus
      ↓
same tasks
      ↓
same evidence
      ↓
compare algorithms fairly
```

This is probably one of the best ideas to steal if you're experimenting with forks, concurrency, retrieval and stopping policies.

---

# 20. My ideal version of this architecture

If I were extending PaperPilot, I'd probably build:

```text
                         QUESTION
                            ↓
                     Planner / Brief
                            ↓
              ┌─────────────┴─────────────┐
              ↓                           ↓
       existing Memory               fresh research
              │                           │
              └─────────────┬─────────────┘
                            ↓
                  Research Controller
                            ↓
            expected-gain / cost scheduler
                            ↓
       ┌────────────┬────────────┬────────────┐
       ↓            ↓            ↓            ↓
    Search        Papers       Browser      Local files
       ↓            ↓            ↓            ↓
       └────────────┴────────────┴────────────┘
                            ↓
                       Evidence
                            ↓
                     Critic / Rubric
                     /            \
                  enough          gap
                    ↓              ↓
                 REPORT       fork/research
                                   │
                                   └───────┐
                                           ↓
                                      Evidence
                                           ↓
                                         ...
```

Then:

```text
Report
  ↓
Human review
  ↓
Wiki
  ↓
Markdown
  ↓
FTS + embeddings + WikiLinks
  ↓
future research
```

with SQLite doing:

```text
checkpointing
+
fast local lexical index
```

but **not being the actual source of truth**. The repo deliberately makes Markdown the canonical knowledge store. [github.com](https://github.com/boluodaixue/paperpilot)

---

## The most useful mental model

When reading this repo, don't think:

> “Where is the code that makes multiple agents?”

Think:

> **“Where is the graph allowed to create another instance of itself, how is that branch represented in state, how is it scheduled, and how are its results merged?”**

And for SQLite, think:

> **“Which SQLite tables are workflow checkpoints, and which are merely search indexes?”**

And for concurrency:

> **“Is concurrency happening at the graph level, coroutine level, or HTTP request level?”**

Those three questions will make the architecture click.

One caveat: the supplied markdown gives the repo's architecture and current directory map, but it does **not include the actual source files**, so I can identify the exact directories and concepts confidently, but I cannot honestly give you exact function names/line numbers for the fork implementation from the attachment alone. The current public repository confirms the architecture described above. Se ha pegado el markdown(2) [github.com](https://github.com/boluodaixue/paperpilot)

f