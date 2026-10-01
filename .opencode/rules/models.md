# Models and routing

This repo switches models. Write for that, not for whichever one is loaded now.

## Changing models
Never edit a model ID in an agent file. Agents ask for a **capability**
(`reasoning`, `coding`, `cheap`); `harness.toml` maps roles to capabilities and
`profiles/*.toml` binds capabilities to OpenRouter IDs.

```
make profiles          # list
make profile P=budget  # switch everything at once
```
Restart opencode after switching — the provider catalog is read at startup.

## Write model-agnostic prompts
- No "you are Claude/GPT/Gemini", no provider-specific formatting tricks, no
  reliance on a particular reasoning-trace style. Harness rankings barely
  transfer between models; instructions tuned to one model are a liability when
  the profile changes.
- Prefer explicit structure (numbered steps, named output fields) over implied
  conventions. Weaker models fail on implication; stronger ones don't mind.
- Assume tool calling may be imperfect. Make every tool-using instruction state
  what to do when the tool fails.

## Routing settings that matter
`require_parameters: true` is the important one. Without it OpenRouter may route
to a provider that silently ignores parameters you sent — tool choice, reasoning
effort, response format — and you will debug the *model* for a day before
suspecting the route.

`transforms: []` is set explicitly and must stay. Omitting the key is not the
same: OpenRouter's middle-out transform activates on over-long prompts and
deletes the middle of your context without telling you. An over-long prompt
should fail loudly so you fix the context.

`data_collection: "deny"` excludes providers that train on traffic. Set
`zdr = true` in harness.toml if you need zero-retention only — it shrinks the
provider pool and can raise latency and price.

## Local models
Point the provider at your own server and keep everything else identical:
```json
{ "provider": { "openrouter": { "options": { "baseURL": "http://localhost:11434/v1" } } } }
```
Expect to loosen nothing else: same permissions, same budgets.

## Cost
Cheap models are not cheap when they loop. A model that needs six turns to do
what another does in two is more expensive at a tenth the price, and burns your
time. Judge by cost-per-completed-task, which `make sessions` reports, not by
the per-token rate.
