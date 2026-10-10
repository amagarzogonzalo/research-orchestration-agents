# 0019 — Loop 2 v2: tiered duplication cleaners (lexical -> semantic -> LLM adjudication)

**Status:** Proposed · **Date:** 2026-10-08 · **Type:** architecture + delivery
**Parent:** 0018 (delta over v1 single-cleaner) · **Related:** 0013, 0017
**Online set (fetched):** `ChenghaoMou/text-dedup` configs+benchmarks, RETSim `arXiv:2311.17264`, LSHBloom `arXiv:2411.04257`, `Ayubjon/minhash-dedupe` (LSH+union-find), `XinshengZhu/NearDuplicateDetection` (SimHash vs MinHash-LSH vs LSA defaults), `othercriteria/ndetect`, Moz near-dup primer, Brenndoerfer MinHash/LSH guide

> For humans: three kinds of mess. Same file twice (easy), almost-same file with
> typos or new DOI (needs fingerprints), same idea reworded (needs meaning check).
> We clean cheap-to-expensive in that order, and only ask the LLM about the hard
> 1%. Old pages are never deleted, just marked superseded with a link.

## 1. What online research forces (delta over v1)

v1 had one cleaner. Evidence says tiered pipeline or false positives/negatives dominate:

- Exact layer is cheap and mandatory: CCNet-style lowercase+normalize then SHA1 per newline unit; `dolma-ngram` Bloom-filter n-gram proportion with T=1.0 default; `text-dedup` `bloom_filter` (error_rate 1e-5) + `suffix_array` (length_threshold 100, merge longest). Use for DOI/URL keys + body-hash equality (already free via `<sha256(url)[:16]>.md`).
- Near-duplicate layer: MinHash 128-256 perms (datasketch recommends 256 for accuracy; `text-dedup` default num_perm 240, ngram 5, threshold 0.7) + LSH banding to avoid O(n2); SimHash 64-bit char shingles n=2..10 tuned, Hamming<=3 (Google 8B-page baseline; `XinshengZhu` default 64 bits, Hamming 3; `text-dedup` hash_bits 64, ngram 3, bit_diff 3). LSHBloom variant puts each band in its own Bloom filter to cut index RAM at internet scale — relevant if wiki grows past 25k pages where Obsidian graph also degrades.
- Benchmark direction (`text-dedup` CORE/NEWS-COPY tables): MinHash macro-F1 ~0.95 beats SimHash ~0.85 on CORE; RETSim-Partial beats MinHash on length-mismatched pairs but MinHash wins when length ratio >1.5x. So keep both: MinHash primary, SimHash fast path for short texts (per `minhash-dedupe`), suffix-array for containment.
- Clustering: union-find transitive closure (A~B,B~C=>one cluster, one keeper) in `minhash-dedupe` + `ndetect` similarity-graph + interactive keep-by-age/size policy. `minhash-dedupe` CLI shape is the model: `--threshold` (default 0.7, sample run 0.6-0.8), `--shingle -k`, `--mode word|char`, report scanned/candidates/confirmed/clusters/kept/removed%.
- Semantic layer only on candidates: embedding cosine (LSA default 0.8 in `XinshengZhu`; vector-dedup grouping pattern) for paraphrase where lexical hashes diverge. RETSim/W4NT3D lesson: lexical methods can't rank among near-dups; embeddings give fine-grained similarity. Keep LLM adjudication <1% pairs (SemHash-LLM pattern from prior loop).

## 2. Proposal v2 for this repo

Pipeline `DedupNode` after relevance, before bodies:
1. Normalize (lowercase, strip bot-wall chrome) -> exact SHA/DOI/URL drop, record `superseded_by` link.
2. Lexical candidates: SimHash prefilter (short) + MinHash-LSH (128 perms dev, 256 prod; bands 16x8 default, Jaccard>=0.6 candidate, >=0.8 merge) over title+abstract+body head. Suffix-array containment check for parent/child.
3. Semantic confirm on candidates only: cosine>=0.8 => duplicate; 0.6-0.8 => LLM adjudicate with source text + grounding invariant from 0018; else keep both + cross-link.
4. Keeper: longest cleanest markdown (prefer crawl4ai/trafilatura over Jina fallback), earliest provenance kept as canonical, rest marked `status: superseded` with timestamp, never deleted (close-don't-delete per gist thread findings). Length-ratio guard: if ratio>1.5x prefer MinHash verdict over embedding.
5. Zero-cost: pure-Python + `hashlib` + stdlib, no new dep. `datasketch`-style logic vendored as small adapter, not a package (AGENTS rule 4).

## 3. SOLID mapping

New `DedupPort` (`candidate_pairs`, `confirm`) with `LexicalDedupAdapter`, `SemanticDedupAdapter`, `LLMAdjudicationAdapter`. `DedupNode` pure, depends on port. OCP: RETSim-style upgrade is a new adapter. Contract tests parametrized over adapters = LSP proof.

## 4. TDD slices

0. Golden pairs fixture (exact, near, paraphrase, containment, distinct) + contract `test_dedup_contract.py` RED.
1. Exact layer green: SHA/DOI drop + Bloom behaviour on fixtures.
2. Lexical green: MinHash/SimHash thresholds + union-find clustering + report counts (mirror `minhash-dedupe report`).
3. Semantic green on candidates only with fake embeddings; LLM adjudication on recorded fixtures only, assert <1% escalation rate logged.
4. Integration: `research()` replay with dedup on stays bit-identical except `superseded_by` metadata; eval reports class shares to resolve 0017 unknown.
