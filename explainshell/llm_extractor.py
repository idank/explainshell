"""
LLM-based man page option extractor.

Public API:
    extract(gz_path, model, **litellm_kwargs) -> store.ManPage
"""

import json
import logging
import os
import re
import subprocess
import time

import litellm

from explainshell import manpage, store

logger = logging.getLogger(__name__)

CHUNK_SIZE_CHARS = 60_000
CHUNK_OVERLAP_CHARS = 2_000

_SYSTEM_PROMPT = """\
You are an expert at parsing Unix man pages. You will be given the plain text of a man page.
Your task is to extract ALL command-line options documented in this man page and return them
as a JSON object.

Rules:
1. Extract every option a user can pass on the command line. Include both short options
   (e.g. -v) and long options (e.g. --verbose). If multiple flags share one description,
   include them all in the same entry.
2. For each option include the full description text exactly as it appears (do not summarize).
3. Set "expects_arg":
   - false  → option takes no argument (e.g. -v, --verbose)
   - true   → option requires an argument (e.g. -f FILE, --file=FILE)
   - a list of strings → fixed set of values (e.g. --color=always|never|auto → ["always","never","auto"])
4. If the option is a positional argument (not preceded by - or --), set "argument" to its
   name (e.g. "FILE"). Leave "short" and "long" as [].
5. Set "nested_cmd" to true only when the argument is itself a shell command
   (e.g. find -exec CMD ;).
6. Do not invent options. Only include options explicitly documented in the text.
7. Return ONLY the JSON object. No markdown, no explanation.

JSON schema:
{
  "options": [
    {
      "short": ["-f"],
      "long": ["--file"],
      "expects_arg": false,
      "argument": null,
      "nested_cmd": false,
      "description": "full description text"
    }
  ]
}"""


class ExtractionError(Exception):
    pass


def get_plain_text(gz_path: str) -> str:
    """Run `mandoc -T txt <gz_path>`, return stdout."""
    result = subprocess.run(
        ["mandoc", "-T", "txt", gz_path],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise ExtractionError(f"mandoc failed for {gz_path}: {result.stderr}")
    return result.stdout


def chunk_text(text: str) -> list:
    """Split at paragraph boundaries if text exceeds CHUNK_SIZE_CHARS."""
    if len(text) <= CHUNK_SIZE_CHARS:
        return [text]

    chunks = []
    paragraphs = text.split("\n\n")
    current = []
    current_len = 0

    for para in paragraphs:
        para_len = len(para) + 2  # +2 for the \n\n
        if current_len + para_len > CHUNK_SIZE_CHARS and current:
            chunk = "\n\n".join(current)
            chunks.append(chunk)
            # overlap: keep trailing paragraphs that fit in CHUNK_OVERLAP_CHARS
            overlap = []
            overlap_len = 0
            for p in reversed(current):
                plen = len(p) + 2
                if overlap_len + plen > CHUNK_OVERLAP_CHARS:
                    break
                overlap.insert(0, p)
                overlap_len += plen
            current = overlap
            current_len = overlap_len

        current.append(para)
        current_len += para_len

    if current:
        chunks.append("\n\n".join(current))

    return chunks


def _get_synopsis_and_aliases(gz_path: str):
    """Reuse manpage.ManPage.read() to get synopsis and aliases from lexgrog."""
    name = manpage.extract_name(gz_path)
    mp_reader = manpage.ManPage(gz_path)
    try:
        mp_reader.read()
    except Exception as e:
        logger.warning("manpage.read() failed for %s: %s", gz_path, e)
        return None, [(name, 10)]

    synopsis = None
    aliases = [(name, 10)]

    if mp_reader.synopsis:
        lines = mp_reader.synopsis.splitlines()
        parsed = [
            manpage._parse_synopsis(gz_path, line)
            for line in lines
            if line.strip()
        ]
        parsed = [p for p in parsed if p]
        if parsed:
            # build alias set like ManPage.parse() does
            import collections
            d = collections.OrderedDict()
            for prog, text in parsed:
                d.setdefault(text, []).append(prog)
            text, progs = list(dict(d).items())[0]
            synopsis = text
            alias_names = set(progs)
            alias_names.discard(name)
            aliases = [(name, 10)] + [(x, 1) for x in alias_names]

    return synopsis, aliases


def _call_llm(chunk: str, chunk_info: str, model: str, litellm_kwargs: dict) -> list:
    """Call LiteLLM, parse JSON, validate. Retries up to 3x on transient errors."""
    user_content = f"Extract all command-line options from this man page{chunk_info}:\n\n{chunk}"
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    kwargs = dict(litellm_kwargs)
    kwargs.setdefault("temperature", 0)
    # request JSON object output where supported
    try:
        kwargs["response_format"] = {"type": "json_object"}
    except Exception:
        pass

    retryable = (
        litellm.RateLimitError,
        litellm.Timeout,
        litellm.ServiceUnavailableError,
        litellm.APIConnectionError,
        litellm.InternalServerError,
    )

    last_err = None
    for attempt in range(3):
        try:
            response = litellm.completion(model=model, messages=messages, **kwargs)
            content = response.choices[0].message.content
            data = _parse_json_response(content)
            _validate_llm_response(data)
            return data["options"]
        except ExtractionError:
            raise
        except retryable as e:
            last_err = e
            wait = 2 ** attempt
            logger.warning(
                "LLM call attempt %d failed (%s), retrying in %ds", attempt + 1, e, wait
            )
            time.sleep(wait)
        except Exception as e:
            raise ExtractionError(f"LLM call failed: {e}") from e

    raise ExtractionError(f"LLM call failed after 3 attempts: {last_err}") from last_err


def _parse_json_response(content: str) -> dict:
    """Strip markdown fences, find outermost {…}, parse JSON."""
    # strip markdown code fences
    content = re.sub(r"^```[^\n]*\n?", "", content.strip())
    content = re.sub(r"\n?```$", "", content.strip())

    # find outermost { ... }
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ExtractionError(f"No JSON object found in LLM response: {content[:200]!r}")

    try:
        return json.loads(content[start : end + 1])
    except json.JSONDecodeError as e:
        raise ExtractionError(f"Invalid JSON from LLM: {e}") from e


def _validate_llm_response(data: dict) -> None:
    """Raises ValueError if data is missing 'options' or options have wrong types."""
    if "options" not in data:
        raise ValueError("LLM response missing 'options' key")
    if not isinstance(data["options"], list):
        raise ValueError("'options' must be a list")
    for item in data["options"]:
        if not isinstance(item, dict):
            raise ValueError(f"Each option must be a dict, got {type(item)}")


def _dedup_options(raw_options: list) -> list:
    """Remove options with duplicate (short+long) flag sets (from chunk overlap)."""
    seen = set()
    result = []
    for opt in raw_options:
        short = tuple(sorted(opt.get("short") or []))
        long = tuple(sorted(opt.get("long") or []))
        key = (short, long)
        # positional args (no flags) always kept
        if not short and not long:
            result.append(opt)
            continue
        if key not in seen:
            seen.add(key)
            result.append(opt)
    return result


def _llm_option_to_store_option(raw: dict, idx: int) -> store.Option:
    """Convert one LLM option dict to a store.Option."""
    short = raw.get("short") or []
    long = raw.get("long") or []
    expects_arg = raw.get("expects_arg", False)
    argument = raw.get("argument") or None
    nested_cmd = bool(raw.get("nested_cmd", False))
    description = raw.get("description", "")

    if not isinstance(short, list):
        raise ValueError(f"'short' must be a list, got {type(short)}")
    if not isinstance(long, list):
        raise ValueError(f"'long' must be a list, got {type(long)}")
    if not isinstance(description, str):
        raise ValueError(f"'description' must be a str, got {type(description)}")

    # nested_cmd requires expects_arg
    if nested_cmd and not expects_arg:
        expects_arg = True

    p = store.Paragraph(idx, description, None, True)
    return store.Option(p, short, long, expects_arg, argument, nested_cmd)


def extract(gz_path: str, model: str, **litellm_kwargs) -> store.ManPage:
    """Full pipeline: gz → plain text → LLM → store.ManPage"""
    synopsis, aliases = _get_synopsis_and_aliases(gz_path)
    plain_text = get_plain_text(gz_path)
    chunks = chunk_text(plain_text)

    all_raw = []
    for i, chunk in enumerate(chunks):
        chunk_info = f" (part {i + 1} of {len(chunks)})" if len(chunks) > 1 else ""
        all_raw.extend(_call_llm(chunk, chunk_info, model, litellm_kwargs))

    all_raw = _dedup_options(all_raw)

    paragraphs = []
    for idx, raw in enumerate(all_raw):
        try:
            paragraphs.append(_llm_option_to_store_option(raw, idx))
        except (AssertionError, ValueError) as e:
            logger.warning("skipping malformed option %d: %s", idx, e)

    return store.ManPage(
        source=os.path.basename(gz_path),
        name=manpage.extract_name(gz_path),
        synopsis=synopsis,
        paragraphs=paragraphs,
        aliases=aliases,
    )
