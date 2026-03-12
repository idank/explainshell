"""
LLM-based man page option extractor.

Public API:
    extract(gz_path, model) -> store.ParsedManpage
"""

import datetime
import hashlib
import json
import logging
import os
import re
import subprocess
import time

import dotenv
import litellm
import openai
from google import genai
from google.genai import types

from explainshell import config, manpage, store
from explainshell.errors import ExtractionError

# Load .env file if present (API keys, etc.). Won't override existing env vars.
dotenv.load_dotenv()

logger = logging.getLogger(__name__)

CHUNK_SIZE_CHARS = 60_000
LLM_TIMEOUT_SECONDS = 300
MAX_MANPAGE_CHARS = 500_000

_MANDOC_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "tools", "mandoc-with-markdown"
)

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
3. Set "has_argument":
   - false  → option takes no argument (e.g. -v, --verbose)
   - true   → option requires an argument (e.g. -f FILE, --file=FILE)
   - a list of strings → fixed set of values (e.g. --color=always|never|auto → ["always","never","auto"])
4. If the option is a positional argument (not preceded by - or --), set "positional" to its
   name (e.g. "FILE"). Leave "short" and "long" as [].
   IMPORTANT: NEVER set "positional" on options that have "short" or "long" flags.
   For example, "-D debugopts" should have "positional": null (not "debugopts") because
   it has short=["-D"]. The "positional" field is ONLY for standalone positional operands.
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
      "has_argument": false,
      "positional": null,
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


def _split_sections(text: str) -> list:
    """Split text into sections at markdown header lines (# or ##).

    Returns a list of (start_line, section_text) tuples where start_line
    is the 1-indexed line number of the first line in the section.
    """
    lines = text.split("\n")
    sections = []
    current_start = 1
    current_lines = []

    for i, line in enumerate(lines):
        if re.match(r"^#{1,2} ", line) and current_lines:
            sections.append((current_start, "\n".join(current_lines)))
            current_start = i + 1  # 1-indexed
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_lines:
        sections.append((current_start, "\n".join(current_lines)))

    return sections


def _build_preamble(text: str) -> str:
    """Extract NAME + SYNOPSIS + first paragraph of DESCRIPTION as preamble."""
    sections = _split_sections(text)
    preamble_headers = {"# NAME", "# SYNOPSIS", "# DESCRIPTION"}
    parts = []
    for _start, section_text in sections:
        header_line = section_text.split("\n", 1)[0].strip()
        if header_line in preamble_headers:
            if header_line == "# DESCRIPTION":
                # Only include the first paragraph of DESCRIPTION.
                paras = section_text.split("\n\n", 2)
                parts.append("\n\n".join(paras[:2]))
            else:
                parts.append(section_text)
    return "\n\n".join(parts)


def chunk_text(text: str) -> list:
    """Split text into numbered chunks at section boundaries.

    Each chunk gets line numbers corresponding to the original text.
    If the text is small enough, returns a single chunk.
    A preamble (NAME/SYNOPSIS/DESCRIPTION intro) is prepended to each
    chunk beyond the first so the model has context.

    When a single section exceeds the chunk size, it is sub-split at
    paragraph (blank-line) boundaries.
    """
    numbered_full, _ = _number_lines(text)
    if len(numbered_full) <= CHUNK_SIZE_CHARS:
        return [numbered_full]

    sections = _split_sections(text)
    preamble = _build_preamble(text)
    preamble_text = ""
    if preamble:
        preamble_text = (
            "[Context — this is a continuation of the same man page]\n\n"
            + preamble
            + "\n\n---\n\n"
        )
    # Reserve space for preamble since any block might land in a non-first chunk
    budget = CHUNK_SIZE_CHARS - len(preamble_text)

    total_lines = text.count("\n") + 1
    width = len(str(total_lines))

    def _number_block(start_line, block_text):
        lines = block_text.split("\n")
        numbered = []
        for j, line in enumerate(lines):
            lineno = start_line + j
            numbered.append(f"{lineno:>{width}}| {line}")
        return "\n".join(numbered)

    def _split_by_lines(start_line, block_text):
        """Last-resort split: cut at line boundaries to fit budget."""
        lines = block_text.split("\n")
        result = []
        cur_lines = []
        cur_start = start_line
        for line in lines:
            candidate = "\n".join(cur_lines + [line])
            if len(_number_block(cur_start, candidate)) > budget and cur_lines:
                result.append((cur_start, "\n".join(cur_lines)))
                cur_start += len(cur_lines)
                cur_lines = []
            cur_lines.append(line)
        if cur_lines:
            result.append((cur_start, "\n".join(cur_lines)))
        return result

    # Build a flat list of (start_line, text) blocks, sub-splitting
    # oversized sections at paragraph boundaries, then by lines if needed.
    blocks = []
    for start_line, section_text in sections:
        numbered = _number_block(start_line, section_text)
        if len(numbered) <= budget:
            blocks.append((start_line, section_text))
        else:
            # Sub-split at paragraph boundaries (\n\n)
            paragraphs = section_text.split("\n\n")
            cur_paras = []
            cur_start = start_line
            for para in paragraphs:
                candidate = "\n\n".join(cur_paras + [para])
                numbered_candidate = _number_block(cur_start, candidate)
                if len(numbered_candidate) > budget and cur_paras:
                    blocks.append((cur_start, "\n\n".join(cur_paras)))
                    cur_start = cur_start + "\n\n".join(cur_paras).count("\n") + 2
                    cur_paras = []
                cur_paras.append(para)
            if cur_paras:
                blocks.append((cur_start, "\n\n".join(cur_paras)))

    # Final pass: split any still-oversized blocks by lines.
    final_blocks = []
    for start_line, block_text in blocks:
        if len(_number_block(start_line, block_text)) > budget:
            final_blocks.extend(_split_by_lines(start_line, block_text))
        else:
            final_blocks.append((start_line, block_text))
    blocks = final_blocks

    # Group blocks into chunks
    chunks = []
    current_parts = []
    current_len = 0

    for start_line, block_text in blocks:
        numbered_block = _number_block(start_line, block_text)
        block_len = len(numbered_block) + 1  # +1 for joining \n

        if current_len + block_len > budget and current_parts:
            chunks.append("\n".join(current_parts))
            current_parts = []
            current_len = 0

        current_parts.append(numbered_block)
        current_len += block_len

    if current_parts:
        chunks.append("\n".join(current_parts))

    # Prepend preamble to chunks after the first.
    if len(chunks) > 1 and preamble_text:
        for i in range(1, len(chunks)):
            chunks[i] = preamble_text + chunks[i]

    return chunks


def _fix_invalid_escapes(s: str) -> str:
    """Replace invalid JSON escape sequences with their escaped form.

    JSON only allows: \\", \\\\, \\/, \\b, \\f, \\n, \\r, \\t, \\uXXXX.
    LLMs sometimes produce things like \\p or \\a which are invalid.
    """
    return re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", s)


def _parse_json_response(content: str) -> dict:
    """Strip markdown fences, find outermost {…}, parse JSON."""
    # strip markdown code fences
    content = re.sub(r"^```[^\n]*\n?", "", content.strip())
    content = re.sub(r"\n?```$", "", content.strip())

    # find outermost { ... }
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        err = ExtractionError(
            f"No JSON object found in LLM response: {content[:200]!r}"
        )
        err.raw_response = content
        raise err

    raw = content[start : end + 1]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # retry after fixing invalid escape sequences
    try:
        return json.loads(_fix_invalid_escapes(raw))
    except json.JSONDecodeError as e:
        err = ExtractionError(f"Invalid JSON from LLM: {e}")
        err.raw_response = content
        raise err from e


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


def _sanitize_option(short, long, has_argument, positional, nested_cmd):
    """Fix common LLM mistakes in option fields.

    Returns (short, long, has_argument, positional, nested_cmd).
    """
    # positional is only for positional operands (no flags)
    if positional and (short or long):
        logger.debug(
            "clearing positional=%r on flagged option %s/%s", positional, short, long
        )
        positional = None

    # nested_cmd requires has_argument
    if nested_cmd and not has_argument:
        has_argument = True

    return short, long, has_argument, positional, nested_cmd


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


def _call_gemini_native(user_content, model):
    """Call Gemini using the native google-genai SDK.

    Returns raw response text.
    """
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    gemini_model = model.removeprefix("gemini/")
    response = client.models.generate_content(
        model=gemini_model,
        contents=user_content,
        config=types.GenerateContentConfig(
            system_instruction=_SYSTEM_PROMPT,
            response_mime_type="application/json",
            http_options=types.HttpOptions(timeout=LLM_TIMEOUT_SECONDS * 1000),
        ),
    )
    return response.text


def _is_openai_model(model):
    """Return True if model should use the native OpenAI SDK."""
    return model.startswith("openai/")


def _call_openai(user_content, model):
    """Call OpenAI using the native Responses API.

    Strips the 'openai/' prefix if present.
    Returns raw response text.
    """
    openai_model = model.removeprefix("openai/")
    client = openai.OpenAI(timeout=LLM_TIMEOUT_SECONDS)
    response = client.responses.create(
        model=openai_model,
        instructions=_SYSTEM_PROMPT,
        input=user_content,
        text={"format": {"type": "json_object"}},
    )
    return response.output_text


def _call_litellm(messages, model):
    """Call any model via litellm (fallback for non-Gemini, non-OpenAI models).

    Returns raw response text.
    """
    kwargs = {}
    try:
        kwargs["response_format"] = {"type": "json_object"}
    except Exception:
        pass
    try:
        info = litellm.get_model_info(model)
        max_out = info.get("max_output_tokens")
        if max_out:
            kwargs["max_tokens"] = max_out
    except Exception:
        pass

    response = litellm.completion(
        model=model,
        messages=messages,
        timeout=LLM_TIMEOUT_SECONDS,
        num_retries=0,
        **kwargs,
    )
    return response.choices[0].message.content


def _call_llm(chunk, chunk_info, model):
    """Call LLM via the appropriate SDK.

    Routing: gemini/ → native Gemini SDK, openai/ → native OpenAI SDK,
    everything else → litellm.

    Retries up to 3x on transient errors.
    Returns (data_dict, messages, raw_response_content).
    """
    user_content = (
        f"Extract all command-line options from this man page{chunk_info}.\n"
        f"Return a JSON object with line numbers from the left margin for each option's range.\n\n{chunk}"
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    use_gemini = model.startswith("gemini/")
    use_openai = not use_gemini and _is_openai_model(model)

    if use_gemini:
        retryable = (Exception,)  # broad catch; filtered below
    elif use_openai:
        retryable = (
            openai.RateLimitError,
            openai.APITimeoutError,
            openai.APIConnectionError,
            openai.InternalServerError,
        )
    else:
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
            if use_gemini:
                content = _call_gemini_native(user_content, model)
            elif use_openai:
                content = _call_openai(user_content, model)
            else:
                content = _call_litellm(messages, model)
            data = _parse_json_response(content)
            _validate_llm_response(data)
            return data, messages, content
        except ExtractionError:
            raise
        except retryable as e:
            if use_gemini:
                # Only retry on transient errors (rate limit, timeout, server errors)
                err_name = type(e).__name__
                status = getattr(e, "status_code", getattr(e, "code", 0)) or 0
                is_transient = (
                    status in (429, 500, 502, 503, 504)
                    or "timeout" in err_name.lower()
                    or "deadline" in str(e).lower()
                )
                if not is_transient:
                    raise ExtractionError(f"LLM call failed: {e}") from e
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
    has_argument = raw.get("has_argument", False)
    positional = raw.get("positional") or None
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

    short, long, has_argument, positional, nested_cmd = _sanitize_option(
        short, long, has_argument, positional, nested_cmd
    )

    return store.Option(
        text=text,
        short=short,
        long=long,
        has_argument=has_argument,
        positional=positional,
        nested_cmd=nested_cmd,
        meta={"lines": [start, end]},
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


def prepare_extraction(gz_path):
    """Pre-process a manpage without calling LLM.

    Returns dict with synopsis, aliases, chunks, original_lines, basename,
    numbered_text, and n_chunks.
    """
    synopsis, aliases = manpage.get_synopsis_and_aliases(gz_path)
    plain_text = get_manpage_text(gz_path)
    basename = os.path.splitext(os.path.splitext(os.path.basename(gz_path))[0])[0]

    if len(plain_text) > MAX_MANPAGE_CHARS:
        logger.warning(
            "%s: skipping, manpage too large for LLM extraction (%s chars, limit %s)",
            basename,
            f"{len(plain_text):,}",
            f"{MAX_MANPAGE_CHARS:,}",
        )
        return None

    numbered_text, original_lines = _number_lines(plain_text)
    chunks = chunk_text(plain_text)

    return {
        "synopsis": synopsis,
        "aliases": aliases,
        "chunks": chunks,
        "original_lines": original_lines,
        "basename": basename,
        "numbered_text": numbered_text,
        "n_chunks": len(chunks),
        "plain_text_len": len(plain_text),
        "plain_text": plain_text,
    }


def build_user_content(chunk, chunk_info):
    """Build the user prompt string for a single chunk."""
    return (
        f"Extract all command-line options from this man page{chunk_info}.\n"
        f"Return a JSON object with line numbers from the left margin for each option's range.\n\n{chunk}"
    )


def process_llm_result(content):
    """Parse and validate a raw LLM response string.

    Returns (data_dict, raw_content).
    """
    data = _parse_json_response(content)
    _validate_llm_response(data)
    return data, content


def _gz_sha256(gz_path):
    """Compute hex SHA-256 digest of a .gz file."""
    h = hashlib.sha256()
    with open(gz_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def finalize_extraction(
    gz_path, prepared, all_chunk_data, debug_dir=None, debug_messages=None
):
    """Assemble a ParsedManpage from prepared data + list of (chunk_data, messages, raw_response) per chunk.

    all_chunk_data: list of (data_dict, messages, raw_response) tuples, one per chunk.
    debug_messages: ignored (kept for API compatibility).

    Returns (ParsedManpage, RawManpage).
    """
    basename = prepared["basename"]
    original_lines = prepared["original_lines"]
    n_chunks = prepared["n_chunks"]
    numbered_text = prepared["numbered_text"]

    if debug_dir:
        os.makedirs(debug_dir, exist_ok=True)
        with open(os.path.join(debug_dir, f"{basename}.md"), "w") as f:
            f.write(numbered_text)

    all_raw = []
    dashless_opts = False
    for i, (chunk_data, messages, raw_response) in enumerate(all_chunk_data):
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

    mp = store.ParsedManpage(
        source=config.source_from_path(gz_path),
        name=manpage.extract_name(gz_path),
        synopsis=prepared["synopsis"],
        options=options,
        aliases=prepared["aliases"],
        dashless_opts=dashless_opts,
    )

    raw_mp = store.RawManpage(
        source_text=prepared["plain_text"],
        generated_at=datetime.datetime.now(datetime.timezone.utc),
        generator="mandoc -T markdown",
        source_gz_sha256=_gz_sha256(gz_path),
    )

    return mp, raw_mp


def _dump_failed_response(fail_dir, basename, chunk_idx, raw_response):
    """Write a failed LLM response to fail_dir for inspection."""
    if not fail_dir:
        return
    os.makedirs(fail_dir, exist_ok=True)
    name = f"{basename}.chunk-{chunk_idx}.failed-response.txt"
    path = os.path.join(fail_dir, name)
    with open(path, "w") as f:
        f.write(raw_response)
    logger.error("raw LLM response saved to %s", path)


def extract(gz_path, model, debug_dir=None, fail_dir=None):
    """LLM extraction pipeline: mandoc → numbered markdown → LLM → (ParsedManpage, RawManpage)."""
    prepared = prepare_extraction(gz_path)
    if prepared is None:
        return None, None
    basename = prepared["basename"]
    chunks = prepared["chunks"]
    n_chunks = prepared["n_chunks"]

    logger.info(
        "%s: %d chars, %d chunk(s)", basename, prepared["plain_text_len"], n_chunks
    )

    def _progress(msg):
        ts = time.strftime("[%H:%M:%S]")
        logger.info("%s %s: %s", ts, basename, msg)

    if n_chunks > 1:
        _progress(f"{len(prepared['numbered_text'])} chars, {n_chunks} chunks")

    all_chunk_data = []
    for i, chunk in enumerate(chunks):
        chunk_info = f" (part {i + 1} of {n_chunks})" if n_chunks > 1 else ""
        chunk_label = f"chunk {i + 1}/{n_chunks}" if n_chunks > 1 else "single chunk"
        logger.info(
            "%s: calling LLM (%s, %d chars)...", basename, chunk_label, len(chunk)
        )
        _progress(f"calling LLM ({chunk_label}, {len(chunk)} chars)...")
        t0 = time.monotonic()
        try:
            chunk_data, messages, raw_response = _call_llm(chunk, chunk_info, model)
        except ExtractionError as e:
            raw = getattr(e, "raw_response", None) or getattr(
                e.__cause__, "raw_response", None
            )
            if raw:
                _dump_failed_response(fail_dir, basename, i, raw)
            raise
        elapsed = time.monotonic() - t0
        n_opts = len(chunk_data["options"])
        logger.info(
            "%s: LLM returned %d option(s) for %s in %.1fs",
            basename,
            n_opts,
            chunk_label,
            elapsed,
        )
        _progress(
            f"LLM returned {n_opts} option(s) for {chunk_label} in {elapsed:.1f}s"
        )
        all_chunk_data.append((chunk_data, messages, raw_response))

    return finalize_extraction(gz_path, prepared, all_chunk_data, debug_dir=debug_dir)


# ---------------------------------------------------------------------------
# Batch API — Gemini
# ---------------------------------------------------------------------------


def _submit_batch_gemini(requests, model):
    """Submit a Gemini batch job with inline requests."""
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    gemini_model = model.removeprefix("gemini/")

    inline_requests = []
    for key, user_content in requests:
        inline_requests.append(
            types.InlinedRequest(
                contents=user_content,
                metadata={"key": key},
                config=types.GenerateContentConfig(
                    system_instruction=_SYSTEM_PROMPT,
                    response_mime_type="application/json",
                ),
            )
        )

    job = client.batches.create(
        model=gemini_model,
        src=inline_requests,
        config=types.CreateBatchJobConfig(display_name="explainshell-batch"),
    )
    return job


def _poll_batch_gemini(client, job_name, poll_interval=30):
    """Poll a Gemini batch job until terminal state."""
    consecutive_errors = 0
    max_consecutive_errors = 5
    while True:
        try:
            job = client.batches.get(name=job_name)
            consecutive_errors = 0
        except Exception as e:
            consecutive_errors += 1
            ts = time.strftime("[%H:%M:%S]")
            logger.warning(
                "%s batch %s: poll error (%d/%d): %s",
                ts,
                job_name,
                consecutive_errors,
                max_consecutive_errors,
                e,
            )
            if consecutive_errors >= max_consecutive_errors:
                raise ExtractionError(
                    f"Batch poll failed after {max_consecutive_errors} consecutive errors: {e}"
                ) from e
            time.sleep(poll_interval)
            continue

        state = job.state.name if hasattr(job.state, "name") else str(job.state)

        if state in ("JOB_STATE_SUCCEEDED", "SUCCEEDED"):
            return job
        if state in ("JOB_STATE_FAILED", "FAILED"):
            raise ExtractionError(f"Batch job failed: {job_name}")
        if state in ("JOB_STATE_CANCELLED", "CANCELLED"):
            raise ExtractionError(f"Batch job cancelled: {job_name}")
        if state in ("JOB_STATE_EXPIRED", "EXPIRED"):
            raise ExtractionError(f"Batch job expired: {job_name}")

        ts = time.strftime("[%H:%M:%S]")
        logger.info(
            "%s batch %s: state=%s, polling again in %ds...",
            ts,
            job_name,
            state,
            poll_interval,
        )
        time.sleep(poll_interval)


def _collect_batch_results_gemini(job):
    """Extract results from a completed Gemini batch job.

    Returns (results, usage) where results maps key -> text and usage is
    {"input_tokens": N, "output_tokens": N}.
    """
    results = {}
    usage = {"input_tokens": 0, "output_tokens": 0}
    if not job.dest or not job.dest.inlined_responses:
        return results, usage
    for resp in job.dest.inlined_responses:
        key = (resp.metadata or {}).get("key", "")
        if resp.response and resp.response.candidates:
            text = resp.response.candidates[0].content.parts[0].text
            results[key] = text
            # Aggregate per-response usage if available.
            resp_usage = getattr(resp.response, "usage_metadata", None)
            if resp_usage:
                usage["input_tokens"] += (
                    getattr(resp_usage, "prompt_token_count", 0) or 0
                )
                usage["output_tokens"] += (
                    getattr(resp_usage, "candidates_token_count", 0) or 0
                )
        else:
            logger.warning("batch response for key %s has no content", key)
    return results, usage


# ---------------------------------------------------------------------------
# Batch API — OpenAI
# ---------------------------------------------------------------------------


def _submit_batch_openai(requests, model):
    """Submit an OpenAI batch job.

    Builds a JSONL file, uploads it, and creates a batch.
    Returns the batch object.
    """
    import io

    openai_model = model.removeprefix("openai/")
    client = openai.OpenAI(timeout=LLM_TIMEOUT_SECONDS)

    # Build JSONL in memory
    buf = io.BytesIO()
    for key, user_content in requests:
        line = json.dumps(
            {
                "custom_id": key,
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": {
                    "model": openai_model,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": user_content},
                    ],
                    "response_format": {"type": "json_object"},
                },
            }
        )
        buf.write(line.encode("utf-8"))
        buf.write(b"\n")
    buf.seek(0)

    # Upload
    file_obj = client.files.create(file=("batch_input.jsonl", buf), purpose="batch")
    logger.info("uploaded batch input file: %s", file_obj.id)

    # Create batch
    batch = client.batches.create(
        input_file_id=file_obj.id,
        endpoint="/v1/chat/completions",
        completion_window="24h",
        metadata={"source": "explainshell"},
    )
    return batch


def _poll_batch_openai(client, batch_id, poll_interval=30):
    """Poll an OpenAI batch until terminal state."""
    consecutive_errors = 0
    max_consecutive_errors = 5
    while True:
        try:
            batch = client.batches.retrieve(batch_id)
            consecutive_errors = 0
        except Exception as e:
            consecutive_errors += 1
            ts = time.strftime("[%H:%M:%S]")
            logger.warning(
                "%s batch %s: poll error (%d/%d): %s",
                ts,
                batch_id,
                consecutive_errors,
                max_consecutive_errors,
                e,
            )
            if consecutive_errors >= max_consecutive_errors:
                raise ExtractionError(
                    f"Batch poll failed after {max_consecutive_errors} consecutive errors: {e}"
                ) from e
            time.sleep(poll_interval)
            continue

        status = batch.status

        counts = batch.request_counts
        counts_str = ""
        if counts:
            counts_str = f" (completed={counts.completed}, failed={counts.failed}, total={counts.total})"

        if status == "completed":
            return batch
        if status == "failed":
            raise ExtractionError(f"Batch job failed: {batch_id}")
        if status == "cancelled":
            raise ExtractionError(f"Batch job cancelled: {batch_id}")
        if status == "expired":
            raise ExtractionError(f"Batch job expired: {batch_id}")

        ts = time.strftime("[%H:%M:%S]")
        logger.info(
            "%s batch %s: status=%s%s, polling again in %ds...",
            ts,
            batch_id,
            status,
            counts_str,
            poll_interval,
        )
        time.sleep(poll_interval)


def _collect_batch_results_openai(batch):
    """Download and parse results from a completed OpenAI batch.

    Returns (results, usage) where results maps key -> text and usage is
    {"input_tokens": N, "output_tokens": N}.
    """
    results = {}
    usage = {"input_tokens": 0, "output_tokens": 0}

    # Prefer batch-level usage if available (newer OpenAI API).
    batch_usage = getattr(batch, "usage", None)
    if batch_usage:
        usage["input_tokens"] = getattr(batch_usage, "input_tokens", 0) or 0
        usage["output_tokens"] = getattr(batch_usage, "output_tokens", 0) or 0

    if not batch.output_file_id:
        return results, usage

    client = openai.OpenAI(timeout=LLM_TIMEOUT_SECONDS)
    content = client.files.content(batch.output_file_id)

    per_request_input = 0
    per_request_output = 0

    for line in content.text.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        key = row.get("custom_id", "")
        response = row.get("response", {})
        body = response.get("body", {})

        # Aggregate per-request token usage.
        req_usage = body.get("usage", {})
        per_request_input += req_usage.get("prompt_tokens", 0)
        per_request_output += req_usage.get("completion_tokens", 0)

        choices = body.get("choices", [])
        if choices:
            text = choices[0].get("message", {}).get("content", "")
            results[key] = text
        else:
            error = row.get("error")
            logger.warning(
                "batch response for key %s has no content (error=%s)", key, error
            )

    # Fall back to per-request totals if batch-level usage was absent.
    if not batch_usage:
        usage["input_tokens"] = per_request_input
        usage["output_tokens"] = per_request_output

    # Log failed requests from error file.
    if batch.error_file_id:
        try:
            error_content = client.files.content(batch.error_file_id)
            for line in error_content.text.splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                key = row.get("custom_id", "unknown")
                error = row.get("error", {})
                logger.warning("batch request %s error: %s", key, error)
        except Exception as e:
            logger.warning("failed to download batch error file: %s", e)

    return results, usage


# ---------------------------------------------------------------------------
# Batch API — unified interface
# ---------------------------------------------------------------------------


def submit_batch(requests, model):
    """Submit a batch job to the appropriate provider.

    Returns a provider-specific batch/job object.
    """
    if model.startswith("gemini/"):
        return _submit_batch_gemini(requests, model)
    if _is_openai_model(model):
        return _submit_batch_openai(requests, model)
    raise ValueError(f"Batch mode is not supported for model: {model}")


def make_batch_client(model):
    """Create a provider-specific client for polling batch jobs."""
    if model.startswith("gemini/"):
        return genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    if _is_openai_model(model):
        return openai.OpenAI(timeout=LLM_TIMEOUT_SECONDS)
    raise ValueError(f"Batch mode is not supported for model: {model}")


def poll_batch(client, job_id, model, poll_interval=30):
    """Poll a batch job until it reaches a terminal state.

    Returns the completed job/batch object.
    """
    if model.startswith("gemini/"):
        return _poll_batch_gemini(client, job_id, poll_interval)
    if _is_openai_model(model):
        return _poll_batch_openai(client, job_id, poll_interval)
    raise ValueError(f"Batch mode is not supported for model: {model}")


def collect_batch_results(job, model):
    """Extract results from a completed batch job.

    Returns (results, usage) where results maps request key → response text,
    and usage is {"input_tokens": N, "output_tokens": N}.
    """
    if model.startswith("gemini/"):
        return _collect_batch_results_gemini(job)
    if _is_openai_model(model):
        return _collect_batch_results_openai(job)
    raise ValueError(f"Batch mode is not supported for model: {model}")
