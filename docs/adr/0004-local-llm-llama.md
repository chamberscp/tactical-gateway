# ADR-0004: Llama 3.x family for local LLM inference

**Status:** Accepted
**Date:** 2026-05-20

## Context

The RAG pipeline needs a local LLM. Constraints:

- Must run locally (air-gapped IATT target).
- Must be open weights and inspectable.
- Should be acceptable to a U.S. government accreditor.
- Dev hardware: 16 GB VRAM (NVIDIA 4070 Ti Super). Prod hardware: TBD,
  expected to be substantially more capable.

## Decision

Use Meta's Llama 3.x family.

- **Development:** Llama 3.1 8B Instruct, Q5_K_M quantization via
  llama.cpp (GGUF). Fits in ~6 GB VRAM, leaves room for embeddings.
- **Production:** Llama 3.3 70B Instruct, Q4_K_M quantization.
  Requires ~40 GB VRAM (A6000-class or 2x L40S).

Inference engine: **llama-cpp-python** (Python bindings to llama.cpp).
Pure C++, auditable, supports both NVIDIA CUDA and CPU fallback.

## Alternatives considered

- **Qwen 2.5 series (Apache 2.0, Chinese provenance).** Excellent quality
  per parameter. Provenance flag during ATO is a real concern even with
  static, inspectable weights. Deferred.
- **Mistral Small 3 / Large 2 (Apache 2.0, French).** Strong models,
  particularly Large 2. Considered as a backup if a Meta licensing or
  policy issue arises.
- **Phi-4 (MIT, Microsoft).** Strong on reasoning, weaker on the
  long-context synthesis we need for OPORD analysis.
- **vLLM as engine instead of llama.cpp.** Faster throughput at scale.
  Heavier dependency footprint and more accreditation surface. Revisit
  for production if throughput becomes a binding constraint.

## Consequences

**Positive:**
- Dev and prod use the same model family — prompts and evals transfer.
- llama.cpp is small, auditable, and well-understood.
- Meta's license permits government use; the license text is short and
  reviewable by counsel.

**Negative:**
- Meta license is not pure open source (OSI definition). Some procurement
  workflows treat it differently from Apache/MIT.
- 70B at Q4 is a noticeable quality drop from full precision; we'll
  measure on representative tasks.

## Revisit if

- Procurement or accreditation rejects the Meta license.
- A newer model with substantially better quality at our scale ships
  (e.g. a hypothetical Llama 4 or improved Mistral).
- Throughput at 100 concurrent users with one GPU is insufficient — then
  evaluate vLLM, batching, or queueing.
