#!/usr/bin/env python3
"""Download everything the service fetches at runtime, at build time instead.

Two things used to be pulled over the network on the first request:

* the Swedish NER model (several hundred MB from Hugging Face), which made the
  first analysis of every container start slow and failed outright on a host
  without egress to huggingface.co;
* tiktoken's encoding files, which are fetched from Azure blob storage the
  first time a text is tokenised. In a restricted network this took chunking
  down with it.

Run during the image build so both are baked into the layer. Honours HF_HOME
and TIKTOKEN_CACHE_DIR, which the Dockerfile sets.
"""

import os
import sys

NER_MODEL = os.getenv("JBG_NER_MODEL", "KBLab/bert-base-swedish-cased-ner")
ENCODINGS = ("o200k_base", "cl100k_base")


def prefetch_tokenizer_encodings() -> None:
    import tiktoken

    for name in ENCODINGS:
        tiktoken.get_encoding(name)
        print(f"  cached tiktoken encoding: {name}")


def prefetch_ner_model() -> None:
    try:
        from transformers import AutoModelForTokenClassification, AutoTokenizer
    except ImportError:
        print("  transformers not installed; skipping NER model (masking disabled)")
        return

    AutoTokenizer.from_pretrained(NER_MODEL)
    AutoModelForTokenClassification.from_pretrained(NER_MODEL)
    print(f"  cached NER model: {NER_MODEL}")


def main() -> int:
    print("Prefetching runtime downloads:")
    failures = []
    for label, step in (("tiktoken", prefetch_tokenizer_encodings), ("NER", prefetch_ner_model)):
        try:
            step()
        except Exception as ex:
            failures.append(f"{label}: {ex}")
            print(f"  FAILED {label}: {ex}", file=sys.stderr)

    if failures:
        # Fail the build: an image that silently falls back to runtime
        # downloads is the problem this script exists to solve.
        print("\nPrefetch failed. Fix the build network or pass --build-arg "
              "SKIP_PREFETCH=1 to accept runtime downloads.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
