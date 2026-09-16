"""Fixed-endpoint theorem retrieval, with bounded responses and explicit failures."""

import json
import httpx

ENDPOINT = "https://leansearch.net/thm/search"


def search_theorems(query: str, num_results: int = 5):
    if not query.strip() or len(query) > 20_000 or not 1 <= num_results <= 10:
        raise ValueError("Use a nonblank query up to 20000 characters and 1–10 results")
    try:
        with httpx.Client(timeout=30, follow_redirects=False) as client:
            with client.stream("POST", ENDPOINT, json={"query": query,
                               "task": "Given a math statement, retrieve useful references, such as theorems, lemmas, and definitions, that are useful for solving the given problem.",
                               "num_results": num_results}) as response:
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 1_000_000:
                        raise ValueError("Theorem service response exceeds 1 MB")
        data = json.loads(body)
        if not isinstance(data, list) or any(not isinstance(x, dict) for x in data):
            raise ValueError("Unexpected theorem service response")
    except (httpx.HTTPError, ValueError) as exc:
        return {"status": "unavailable", "query": query, "endpoint": ENDPOINT,
                "error": str(exc), "results": [],
                "meaning": "Retrieval failed; this is not evidence that a theorem or proof does not exist."}
    return {"status": "ok", "query": query, "endpoint": ENDPOINT,
            "results": [{k: str(row.get(k, "")) for k in ("title", "theorem", "arxiv_id", "theorem_id")}
                        for row in data[:num_results]],
            "meaning": "Search leads only. Read source definitions and proofs before citing."}
