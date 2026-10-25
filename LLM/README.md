# LLM: the local models (ADR-026)

KnowledgeAI answers questions with models that run on our own machine: no paid API, no account, no per-question
cost. This folder holds them.

| File (in `models/`) | Role | Source (pinned revision) | Size | License |
| --- | --- | --- | --- | --- |
| `Qwen3-4B-Instruct-2507-Q4_K_M.gguf` | Writes the answers (instruction-tuned, 4B parameters, 4-bit) | `unsloth/Qwen3-4B-Instruct-2507-GGUF` @ `a06e946` (GGUF conversion of `Qwen/Qwen3-4B-Instruct-2507`) | 2.5 GB | Apache-2.0 |
| `nomic-embed-text-v1.5.Q8_0.gguf` | Turns chunks and questions into 768-number vectors for search | `nomic-ai/nomic-embed-text-v1.5-GGUF` @ `0188c9b` | 0.15 GB | Apache-2.0 |
| `bge-reranker-v2-m3-Q8_0.gguf` | Scores how well each found passage answers the question (optional, ADR-028) | `gpustack/bge-reranker-v2-m3-GGUF` @ `3093af0` (GGUF of `BAAI/bge-reranker-v2-m3`) | 0.64 GB | Apache-2.0 |

`models.json` pins each file's repository, revision and SHA-256. The model files themselves are git-ignored.

## Get them

```sh
backend/.venv/Scripts/python scripts/download_models.py          # downloads what's missing, verifies every file
backend/.venv/Scripts/python scripts/download_models.py --check  # verify only
```

## Run them

llama.cpp's server (an OpenAI-compatible API) serves each model; compose runs both:

```sh
docker compose --profile llm up -d                                   # CPU (any machine, e.g. the VM)
docker compose -f compose.yaml -f compose.gpu.yaml --profile llm up -d   # NVIDIA GPU (this laptop's RTX 4050)
curl http://127.0.0.1:8080/health   # answers model
curl http://127.0.0.1:8081/health   # embedding model
curl http://127.0.0.1:8082/health   # reranker
```

The app talks to them with `LLM_PROVIDER=local`, `EMBEDDING_PROVIDER=local` and the URLs in `.env.example`
("Local models"). Containers in the `app` profile use them automatically.

## Why these two

- **Qwen3-4B-Instruct-2507:** the strongest instruction-following model that fits entirely in a 6 GB laptop GPU
  (about 3 GB with its working memory) and still runs on a CPU-only VM. It reliably fills the strict JSON
  answer format (the server turns the schema into a grammar). Measured here: ~38 tokens/s on the RTX 4050.
- **nomic-embed-text-v1.5:** a small, well-rated retrieval embedding model with an official GGUF build. It uses
  task prefixes (`search_query: `, `search_document: `), which the app adds.

Changing either model: add it to `models.json`, download, then follow docs/deployment.md §7 (a new embedding
model means a new Chroma collection and a rebuild; ADR-013) and re-run the golden gates.
