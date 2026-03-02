"""
LLM-ref man page option extractor — line-reference based.

Instead of asking the LLM to repeat description text, this extractor sends
line-numbered markdown and asks the LLM to return only metadata + line ranges.
Descriptions are then sliced directly from the source markdown — verbatim.

Public API:
    extract(gz_path, model, **litellm_kwargs) -> store.ParsedManpage
"""

import json
import logging
import os
import time

import litellm

from explainshell import config, manpage, store
from explainshell.errors import ExtractionError
from explainshell.llm_extractor import (
    LLM_TIMEOUT_SECONDS,
    _dedup_options,
    _parse_json_response,
    _validate_llm_response,
    chunk_text,
    get_manpage_text,
)

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are an expert at parsing Unix man pages. You will be given a markdown-formatted man page
with line numbers in the format "  42| content here". Your task is to extract ALL command-line
options documented in this man page and return them as a JSON object.

Rules:
1. Extract every option a user can pass on the command line. Include both short options
   (e.g. -v) and long options (e.g. --verbose). If multiple flags share one description,
   include them all in the same entry.
2. For each option, provide "lines": [start, end] where start is the line number of the
   flag/usage line and end is the line number of the last line of its description.
   Use the line numbers shown in the left margin. Include ALL description lines — do not
   stop early.
3. Set "expects_arg":
   - false  → option takes no argument (e.g. -v, --verbose)
   - true   → option requires an argument (e.g. -f FILE, --file=FILE)
   - a list of strings → fixed set of values (e.g. --color=always|never|auto → ["always","never","auto"])
4. If the option is a positional argument (not preceded by - or --), set "argument" to its
   name (e.g. "FILE"). Leave "short" and "long" as [].
5. Set "nested_cmd" to true only when the argument is itself a shell command
   (e.g. find -exec CMD ;).
6. Do not invent options. Only include options explicitly documented in the text.
7. Return ONLY the JSON object. No markdown fences, no explanation.
8. Set "dashless_opts" to true if the man page documents that options can be
   specified without a leading dash (e.g., "traditional usage", "BSD-style
   options", or usage examples like `tar xzvf`). Otherwise set it to false.

JSON schema:
{
  "dashless_opts": false,
  "options": [
    {
      "short": ["-f"],
      "long": ["--file"],
      "expects_arg": false,
      "argument": null,
      "nested_cmd": false,
      "lines": [111, 115]
    }
  ]
}"""


def _number_lines(text):
    """Add line numbers to every line of text.

    Returns (numbered_text, original_lines) where original_lines is a
    dict mapping 1-indexed line numbers to their original content.
    """
    lines = text.split("\n")
    original_lines = {}
    numbered = []
    width = len(str(len(lines)))
    for i, line in enumerate(lines, 1):
        original_lines[i] = line
        numbered.append(f"{i:>{width}}| {line}")
    return "\n".join(numbered), original_lines


def _extract_text_from_lines(original_lines, start, end):
    """Build description text from line range [start, end] (1-indexed, inclusive).

    The first line is treated as the flag line. Remaining lines form the
    description body. Blockquote prefixes ("> ") are stripped from all lines.
    """
    if start < 1 or end < start:
        return ""
    selected = []
    for i in range(start, end + 1):
        line = original_lines.get(i, "")
        # Strip blockquote prefix
        if line.startswith("> "):
            line = line[2:]
        selected.append(line)

    if not selected:
        return ""

    # First line is the flag line; rest is the description body.
    flag_line = selected[0]
    body_lines = selected[1:]

    # Strip leading blank lines from body
    while body_lines and not body_lines[0].strip():
        body_lines.pop(0)

    if body_lines:
        return flag_line + "\n\n" + "\n".join(body_lines)
    return flag_line


def _call_llm_ref(chunk, chunk_info, model, litellm_kwargs):
    """Call LiteLLM with the line-reference prompt. Retries up to 3x on transient errors.

    Returns (data_dict, messages, raw_response_content).
    """
    user_content = (
        f"Extract all command-line options from this man page{chunk_info}.\n"
        f"Return line numbers from the left margin for each option's range.\n\n{chunk}"
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    kwargs = dict(litellm_kwargs)
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
            response = litellm.completion(
                model=model,
                messages=messages,
                timeout=LLM_TIMEOUT_SECONDS,
                num_retries=0,
                **kwargs,
            )
            content = response.choices[0].message.content
            data = _parse_json_response(content)
            _validate_llm_response(data)
            return data, messages, content
        except ExtractionError:
            raise
        except retryable as e:
            last_err = e
            wait = 2**attempt
            logger.warning(
                "LLM call attempt %d failed (%s), retrying in %ds", attempt + 1, e, wait
            )
            time.sleep(wait)
        except Exception as e:
            raise ExtractionError(f"LLM call failed: {e}") from e

    raise ExtractionError(f"LLM call failed after 3 attempts: {last_err}") from last_err


def _ref_option_to_store_option(raw, original_lines):
    """Convert one LLM-ref option dict to a store.Option.

    Uses the "lines" field to slice the description from original_lines.
    """
    short = raw.get("short") or []
    long = raw.get("long") or []
    expects_arg = raw.get("expects_arg", False)
    argument = raw.get("argument") or None
    nested_cmd = bool(raw.get("nested_cmd", False))

    if not isinstance(short, list):
        raise ValueError(f"'short' must be a list, got {type(short)}")
    if not isinstance(long, list):
        raise ValueError(f"'long' must be a list, got {type(long)}")

    lines = raw.get("lines")
    if not lines or not isinstance(lines, list) or len(lines) != 2:
        raise ValueError(f"'lines' must be a [start, end] list, got {lines!r}")
    start, end = int(lines[0]), int(lines[1])
    text = _extract_text_from_lines(original_lines, start, end)

    if nested_cmd and not expects_arg:
        expects_arg = True

    return store.Option(
        text=text,
        short=short,
        long=long,
        expects_arg=expects_arg,
        argument=argument,
        nested_cmd=nested_cmd,
    )


def _dedup_ref_options(raw_options):
    """Dedup options using synthetic description length based on line span.

    This wraps _dedup_options by injecting a synthetic "description" key
    so that the longest-span entry wins during dedup.
    """
    for opt in raw_options:
        lines = opt.get("lines")
        if lines and isinstance(lines, list) and len(lines) == 2:
            opt["description"] = "x" * (int(lines[1]) - int(lines[0]) + 1)
        elif "description" not in opt:
            opt["description"] = ""
    return _dedup_options(raw_options)


def extract(gz_path, model, debug_dir=None, **litellm_kwargs):
    """LLM-ref extraction pipeline: mandoc → numbered markdown → LLM → ParsedManpage."""
    synopsis, aliases = manpage.get_synopsis_and_aliases(gz_path)

    plain_text = get_manpage_text(gz_path)
    numbered_text, original_lines = _number_lines(plain_text)
    chunks = chunk_text(numbered_text)

    basename = os.path.splitext(os.path.splitext(os.path.basename(gz_path))[0])[0]
    n_chunks = len(chunks)
    logger.info("%s: %d chars, %d chunk(s)", basename, len(plain_text), n_chunks)

    def _progress(msg):
        ts = time.strftime("[%H:%M:%S]")
        print(f"{ts} {basename}: {msg}")
    if n_chunks > 1:
        _progress(f"{len(numbered_text)} chars, {n_chunks} chunks")

    if debug_dir:
        os.makedirs(debug_dir, exist_ok=True)
        with open(os.path.join(debug_dir, f"{basename}.md"), "w") as f:
            f.write(numbered_text)

    all_raw = []
    dashless_opts = False
    for i, chunk in enumerate(chunks):
        chunk_info = f" (part {i + 1} of {n_chunks})" if n_chunks > 1 else ""
        chunk_label = f"chunk {i + 1}/{n_chunks}" if n_chunks > 1 else "single chunk"
        logger.info(
            "%s: calling LLM (%s, %d chars)...", basename, chunk_label, len(chunk)
        )
        _progress(f"calling LLM ({chunk_label}, {len(chunk)} chars)...")
        t0 = time.monotonic()
        chunk_data, messages, raw_response = _call_llm_ref(
            chunk, chunk_info, model, litellm_kwargs
        )
        elapsed = time.monotonic() - t0
        n_opts = len(chunk_data["options"])
        logger.info(
            "%s: LLM returned %d option(s) for %s in %.1fs",
            basename,
            n_opts,
            chunk_label,
            elapsed,
        )
        _progress(f"LLM returned {n_opts} option(s) for {chunk_label} in {elapsed:.1f}s")
        all_raw.extend(chunk_data["options"])
        if chunk_data.get("dashless_opts"):
            dashless_opts = True

        if debug_dir:
            if n_chunks == 1:
                prompt_name = f"{basename}.prompt.json"
                response_name = f"{basename}.response.txt"
            else:
                prompt_name = f"{basename}.chunk-{i}.prompt.json"
                response_name = f"{basename}.chunk-{i}.response.txt"
            with open(os.path.join(debug_dir, prompt_name), "w") as f:
                json.dump(messages, f, indent=2)
            with open(os.path.join(debug_dir, response_name), "w") as f:
                f.write(raw_response)

    all_raw = _dedup_ref_options(all_raw)

    options = []
    for idx, raw in enumerate(all_raw):
        try:
            options.append(_ref_option_to_store_option(raw, original_lines))
        except (AssertionError, ValueError) as e:
            logger.warning("skipping malformed option %d: %s", idx, e)

    logger.info("%s: extracted %d option(s) total", basename, len(options))

    return store.ParsedManpage(
        source=config.source_from_path(gz_path),
        name=manpage.extract_name(gz_path),
        synopsis=synopsis,
        options=options,
        aliases=aliases,
        dashless_opts=dashless_opts,
    )
