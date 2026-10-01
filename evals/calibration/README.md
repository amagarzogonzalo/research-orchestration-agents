Put two real artifacts here, named `<name>.expect<N>.md`:

- one you scored high (e.g. `good.expect9.md`)
- one that is fluent, well-structured and empty (e.g. `padded.expect3.md`)

The second is the important one — it catches a judge that rewards polish.
Until both exist the calibration step is skipped and judge verdicts are trusted
on faith, which is exactly the failure mode this directory prevents.
