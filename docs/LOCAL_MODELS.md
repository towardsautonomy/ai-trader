# Local models

Local models cost nothing per call and never leave the machine. They are slower than hosted ones, and below roughly
27B parameters they are not good enough for this desk.

## Recommended: Qwen3.8 27B at 8-bit

Measured on an RTX A6000 (48 GB), 2026-09-22/23:

| Model | Result on `./scripts/trader llm test` and in paper runs |
|---|---|
| llama3.2 3B, 8B-class | copies worked examples from the prompt instead of reading the data |
| qwen2.5:14b | answers are original and well-formed, but all four specialists paraphrase the regime with one confidence: the swarm collapses to one voice and skips everything |
| deepseek-r1:32b | the same one-voice collapse (every specialist bearish 1.00) |
| **qwen3.8:27b Q8** | specialists disagree for concrete reasons, the skeptic finds real flaws in the data, zero unusable answers; a full paper session with zero errors |

## Ollama setup

```bash
./scripts/trader llm pull qwen3.8:27b-q8_0                  # 30 GB; needs Ollama 0.34+
printf 'FROM qwen3.8:27b-q8_0\nPARAMETER num_ctx 16384\n' > /tmp/Modelfile
ollama create qwen3.8:27b-q8-16k -f /tmp/Modelfile  # 16K context: the default 262K fills 45 GB of VRAM
./scripts/trader llm test local/qwen3.8:27b-q8-16k NVDA
./scripts/trader start --data robinhood --llm local/qwen3.8:27b-q8-16k
```

The largest prompt the desk sends is about 7,300 tokens (the scout's universe table), so 16K leaves room for thinking.
`AIT_LOCAL_LLM_URL` is detected automatically when Ollama answers on `127.0.0.1:11434`.

## Thinking

Qwen3.8 can reason before answering. Thinking is set per agent in `config.yaml` (`agents.thinking`; default for the
rest: `AIT_LOCAL_LLM_THINKING`, `none`). Only the decision seats think:

| Agent | Thinking | Typical time on Q8 |
|---|---|---|
| regime, scout, specialists | off | 8–40 s |
| skeptic, position manager | low | ~2 min |
| portfolio manager, reviewer | medium | ~1.5–3.5 min |

With thinking on, the reply budget is 6,000 tokens and the timeout at least 360 s; with it off, the thinking would
otherwise eat the 1,200-token budget and leave an empty answer. The reasoning is stored with each call.

## Throughput, and why Ollama cannot parallelise this model

This GPU produces about 20 tokens per second on the 27B Q8 model. One candidate takes about 4.5 minutes with the
thinking seats, so the engine is set to two candidates per cycle, one at a time (`engine.max_candidates_per_cycle: 2`,
`engine.parallel_candidates: 1`): about 60 candidates per session.

Ollama 0.34 does not batch requests for Qwen3.8's architecture (`qwen35`): it logs "model architecture does not
currently support parallel requests" and ignores `OLLAMA_NUM_PARALLEL`. Measured: four requests at once took 72 s with
one slot and 65 s with four. Real batching needs another server; see vLLM below.

## vLLM

vLLM serves many requests at once through continuous batching, so parallel agents share the GPU instead of queueing.
It runs from its own environment, no root needed, and speaks the same OpenAI-compatible API, so the engine only needs a
different `AIT_LOCAL_LLM_URL`.

(Setup and measured results are added once benchmarked on this machine.)
