# ADR 0002 — Local-first providers behind adapters

Status: accepted · Date: 2026-09-13

## Context
§41 requires provider portability; the owner wants to run offline first.

## Decision
Every external dependency is an adapter with a local default: Ollama for LLM and embeddings, the filesystem for the object store, SearXNG for web search, PostgreSQL+pgvector for storage and retrieval. Provider identity (model name, dimension, extractor/prompt version) is stored on every artefact it produces.

## Consequences
* Moving to a cloud model or S3 bucket is configuration (`KP_*` variables), not code.
* Vectors from different embedding models never mix because the identity is stored with each vector; changing the model requires a planned re-embedding job.
