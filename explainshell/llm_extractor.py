"""
LLM-based man page option extractor.

Public API:
    extract(gz_path, model, **litellm_kwargs) -> store.ParsedManpage
"""

import json
import logging
import os
import re
import subprocess
import time

import litellm

from explainshell import config, manpage, store
from explainshell.errors import ExtractionError

logger = logging.getLogger(__name__)

CHUNK_SIZE_CHARS = 60_000
CHUNK_OVERLAP_CHARS = 2_000
LLM_TIMEOUT_SECONDS = 300

_MANDOC_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tools", "mandoc-with-markdown")

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
   IMPORTANT: NEVER set "argument" on options that have "short" or "long" flags.
   For example, "-D debugopts" should have "argument": null (not "debugopts") because
   it has short=["-D"]. The "argument" field is ONLY for standalone positional operands.
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


def get_manpage_text(gz_path: str) -> str:
    """Run patched `mandoc -T markdown <gz_path>` to get markdown directly."""
    result = subprocess.run(
        [_MANDOC_PATH, "-T", "markdown", gz_path],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise ExtractionError(f"mandoc failed for {gz_path}: {result.stderr}")

    return result.stdout.strip()


# Keep backward-compatible alias
get_plain_text = get_manpage_text


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


def _fix_invalid_escapes(s: str) -> str:
    """Replace invalid JSON escape sequences with their escaped form.

    JSON only allows: \\", \\\\, \\/, \\b, \\f, \\n, \\r, \\t, \\uXXXX.
    LLMs sometimes produce things like \\p or \\a which are invalid.
    """
    return re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', s)


def _parse_json_response(content: str) -> dict:
    """Strip markdown fences, find outermost {…}, parse JSON."""
    # strip markdown code fences
    content = re.sub(r"^```[^\n]*\n?", "", content.strip())
    content = re.sub(r"\n?```$", "", content.strip())

    # find outermost { ... }
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ExtractionError(
            f"No JSON object found in LLM response: {content[:200]!r}"
        )

    raw = content[start : end + 1]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # retry after fixing invalid escape sequences
    try:
        return json.loads(_fix_invalid_escapes(raw))
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
    """Remove options with duplicate (short+long) flag sets (from chunk overlap).

    When duplicates exist, keep the entry with the longest description so that
    detailed sections win over brief summary entries.
    """
    best = {}  # key -> (index, opt)
    positional = []  # (index, opt) for keyless entries
    for idx, opt in enumerate(raw_options):
        try:
            raw_short = opt.get("short") or []
            raw_long = opt.get("long") or []
            if not isinstance(raw_short, list) or not isinstance(raw_long, list):
                positional.append((idx, opt))
                continue
            short = tuple(sorted(str(s) for s in raw_short))
            long = tuple(sorted(str(s) for s in raw_long))
        except (TypeError, AttributeError):
            positional.append((idx, opt))
            continue
        key = (short, long)
        # positional args (no flags) always kept
        if not short and not long:
            positional.append((idx, opt))
            continue
        prev = best.get(key)
        if prev is None:
            best[key] = (idx, opt)
        else:
            old_desc = len(prev[1].get("description") or "")
            new_desc = len(opt.get("description") or "")
            if new_desc > old_desc:
                best[key] = (idx, opt)
    # Merge and sort by original insertion order
    all_entries = list(best.values()) + positional
    all_entries.sort(key=lambda x: x[0])
    return [opt for _, opt in all_entries]


def _sanitize_option(short, long, expects_arg, argument, nested_cmd):
    """Fix common LLM mistakes in option fields.

    Returns (short, long, expects_arg, argument, nested_cmd).
    """
    # argument is only for positional operands (no flags)
    if argument and (short or long):
        logger.debug("clearing argument=%r on flagged option %s/%s", argument, short, long)
        argument = None

    # nested_cmd requires expects_arg
    if nested_cmd and not expects_arg:
        expects_arg = True

    return short, long, expects_arg, argument, nested_cmd


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


def _call_llm(chunk, chunk_info, model, litellm_kwargs):
    """Call LiteLLM. Retries up to 3x on transient errors.

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


def _llm_option_to_store_option(raw, original_lines):
    """Convert one LLM option dict to a store.Option.

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

    short, long, expects_arg, argument, nested_cmd = _sanitize_option(
        short, long, expects_arg, argument, nested_cmd
    )

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
    """LLM extraction pipeline: mandoc → numbered markdown → LLM → ParsedManpage."""
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
        chunk_data, messages, raw_response = _call_llm(
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
            options.append(_llm_option_to_store_option(raw, original_lines))
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
