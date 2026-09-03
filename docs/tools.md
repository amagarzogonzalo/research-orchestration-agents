For your agent's external toolset, there are several excellent free scholarly APIs you can integrate to fetch academic papers, metadata, and citation networks. By wrapping these APIs into a Model Context Protocol (MCP) server, your agents can query them dynamically:

* **OpenAlex:** This is a fully open catalog with over 250 million works across all scientific disciplines. As of February 2026, you need to register for a free API key, which grants you a generous allowance of 100,000 credits (equivalent to $1) per day for complex searches. A massive advantage for agentic workflows is that looking up single records by their ID or DOI remains completely free and does not consume your daily credit allowance.


* **Semantic Scholar:** Backed by the Allen Institute for AI, this database indexes over 200 million papers and is exceptionally strong for computer science, AI, and engineering research. They offer a free API with a public rate limit of 1000 requests per second shared among unauthenticated users, which provides more than enough bandwidth for an agent operating on a free-tier LLM.


* **PubMed:** If your agents need biomedical and life sciences data, PubMed offers over 40 million records. It is entirely free to query programmatically via their Entrez Programming Utilities (E-utilities).


* **Crossref & arXiv:** Crossref serves as an essential open metadata backbone for resolving DOI-centric queries and validating citations across publishers. For preprints, the arXiv API offers direct, free access to the latest research in physics, mathematics, and computer science.



To implement this efficiently, practitioners often use existing federated MCP servers (like the open-source `paper-search-mcp`), which allow a single LLM tool call to simultaneously query OpenAlex, Semantic Scholar, Crossref, and arXiv, returning a synthesized list of validated academic sources.