#!/usr/bin/env bash
# Start Ollama with the memory settings this project is tuned for (foreground; Ctrl-C to stop).
#   flash attention + q8 KV cache: smaller KV cache for the same context
#   8K context: plenty for one page of dialogue; keeps the model at ~5.7GB resident
export OLLAMA_FLASH_ATTENTION=1
export OLLAMA_KV_CACHE_TYPE=q8_0
export OLLAMA_CONTEXT_LENGTH="${OLLAMA_CONTEXT_LENGTH:-8192}"
exec ollama serve
