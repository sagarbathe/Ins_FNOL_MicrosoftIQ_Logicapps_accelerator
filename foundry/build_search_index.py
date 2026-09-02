
    """Chunk, embed, and index the Auto FNOL knowledge base documents into Azure AI Search."""
    import glob
    import os
    import re
    import subprocess
    import sys

    import requests

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    import config

    KB_DOCS_DIR = os.path.join(os.path.dirname(__file__), "kb_docs")
    SEARCH_ENDPOINT = config.AZURE_SEARCH_ENDPOINT.rstrip("/")
    SEARCH_API_VERSION = config.AZURE_SEARCH_API_VERSION
    INDEX_NAME = config.AZURE_SEARCH_INDEX_NAME
    AOAI_ENDPOINT = config.AZURE_OPENAI_ENDPOINT.rstrip("/")
    AOAI_API_VERSION = config.AZURE_OPENAI_API_VERSION
    EMBED_DEPLOYMENT = config.AZURE_OPENAI_EMBEDDING_DEPLOYMENT
    EMBED_DIM = config.AZURE_OPENAI_EMBEDDING_DIM
    SEARCH_KEY = config.AZURE_SEARCH_ADMIN_KEY


    def get_aad_token(resource: str) -> str:
        out = subprocess.check_output(
            ["az", "account", "get-access-token", "--resource", resource, "--query", "accessToken", "-o", "tsv"],
            shell=True,
            text=True,
        )
        return out.strip()


    def search_headers() -> dict:
        if SEARCH_KEY:
            return {"api-key": SEARCH_KEY, "Content-Type": "application/json"}
        token = get_aad_token("https://search.azure.com")
        return {"Authorization": "Bearer " + token, "Content-Type": "application/json"}


    def chunk_text(text, doc_id, title, max_words=180, overlap=30):
        paragraphs = [p.strip() for p in text.split("

") if p.strip()]
        chunks = []
        current_words = []
        for para in paragraphs:
            words = para.split()
            if len(current_words) + len(words) > max_words and current_words:
                chunks.append(" ".join(current_words))
                current_words = current_words[-overlap:] if overlap < len(current_words) else current_words
            current_words.extend(words)
        if current_words:
            chunks.append(" ".join(current_words))
        return [
            {
                "id": f"{doc_id}-chunk-{i:03d}",
                "docId": doc_id,
                "title": title,
                "chunkIndex": i,
                "content": chunk,
            }
            for i, chunk in enumerate(chunks)
        ]


    def embed_batch(texts, token):
        url = f"{AOAI_ENDPOINT}/openai/deployments/{EMBED_DEPLOYMENT}/embeddings?api-version={AOAI_API_VERSION}"
        headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
        resp = requests.post(url, headers=headers, json={"input": texts}, timeout=60)
        resp.raise_for_status()
        return [item["embedding"] for item in resp.json()["data"]]


    def create_index():
        url = f"{SEARCH_ENDPOINT}/indexes/{INDEX_NAME}?api-version={SEARCH_API_VERSION}"
        schema = {
            "name": INDEX_NAME,
            "fields": [
                {"name": "id", "type": "Edm.String", "key": True, "filterable": True},
                {"name": "docId", "type": "Edm.String", "filterable": True, "facetable": True},
                {"name": "title", "type": "Edm.String", "searchable": True, "filterable": True},
                {"name": "chunkIndex", "type": "Edm.Int32", "filterable": True, "sortable": True},
                {"name": "content", "type": "Edm.String", "searchable": True},
                {
                    "name": "contentVector",
                    "type": "Collection(Edm.Single)",
                    "searchable": True,
                    "dimensions": EMBED_DIM,
                    "vectorSearchProfile": "vec-profile",
                },
            ],
            "vectorSearch": {
                "algorithms": [{"name": "hnsw-cfg", "kind": "hnsw"}],
                "profiles": [{"name": "vec-profile", "algorithm": "hnsw-cfg"}],
            },
            "semantic": {
                "configurations": [{
                    "name": "default-semantic",
                    "prioritizedFields": {
                        "titleField": {"fieldName": "title"},
                        "prioritizedContentFields": [{"fieldName": "content"}],
                    },
                }]
            },
        }
        resp = requests.put(url, headers=search_headers(), json=schema, timeout=60)
        print("createIndex:", resp.status_code, resp.text[:500])
        resp.raise_for_status()


    def upload_docs(docs):
        url = f"{SEARCH_ENDPOINT}/indexes/{INDEX_NAME}/docs/index?api-version={SEARCH_API_VERSION}"
        payload = {"value": [{"@search.action": "mergeOrUpload", **d} for d in docs]}
        resp = requests.post(url, headers=search_headers(), json=payload, timeout=60)
        print("uploadDocs:", resp.status_code)
        if resp.status_code >= 300:
            print(resp.text[:1000])
        resp.raise_for_status()


    def main():
        create_index()
        aad_token = get_aad_token("https://cognitiveservices.azure.com")
        all_chunks = []
        for path in sorted(glob.glob(os.path.join(KB_DOCS_DIR, "*.md"))):
            doc_id = os.path.splitext(os.path.basename(path))[0]
            with open(path, encoding="utf-8") as f:
                text = f.read()
            title_match = re.match(r"#\s*(.+)", text)
            title = title_match.group(1).strip() if title_match else doc_id
            chunks = chunk_text(text, doc_id, title)
            all_chunks.extend(chunks)
            print(f"{doc_id}: {len(chunks)} chunks")
        print(f"Total chunks: {len(all_chunks)}")
        batch_size = 16
        for i in range(0, len(all_chunks), batch_size):
            batch = all_chunks[i:i + batch_size]
            embeddings = embed_batch([c["content"] for c in batch], aad_token)
            for chunk, emb in zip(batch, embeddings):
                chunk["contentVector"] = emb
            print(f"embedded batch {i}-{i + len(batch)}")
        for i in range(0, len(all_chunks), batch_size):
            upload_docs(all_chunks[i:i + batch_size])
        print("DONE")


    if __name__ == "__main__":
        main()
