---
name: extract-manpage
description: Extract options from a manpage .gz file and add the result to a database file. Use when the user wants to extract, parse, or import a man page into the explainshell database.
user_invocable: true
---

# extract-manpage

You ARE the extractor. Read a manpage, identify all command-line options, and store the results in the database. Do NOT call any external LLM API.

## Usage

```
/extract-manpage <gz_file> [--db <db_file>]
```

## Arguments

- **gz_file** (required): Path to the `.gz` manpage file.
- **db_file** (optional): Path to the SQLite database file. Defaults to `explainshell.db` in the project root.

## Step 1: Convert the manpage to markdown

Run this command to convert the .gz file to markdown and save it to a temp file:

```bash
/home/idank/dev/vibe/explainshell/tools/mandoc-with-markdown -T markdown <gz_file> > /tmp/manpage_extract.md
```

## Step 2: Read the markdown

Use the Read tool to read `/tmp/manpage_extract.md`. The Read tool output shows line numbers — these are the line numbers you will reference in your extraction.

If the file is longer than 2000 lines, read it in chunks using the `offset` and `limit` parameters.

## Step 3: Extract all command-line options

Analyze the markdown and extract every command-line option. For each option, record:

- **short**: list of short flags (e.g. `["-v"]`). Empty list if none.
- **long**: list of long flags (e.g. `["--verbose"]`). Empty list if none.
- **expects_arg**:
  - `false` — option takes no argument (e.g. `-v`, `--verbose`)
  - `true` — option requires an argument (e.g. `-f FILE`, `--file=FILE`)
  - a list of strings — fixed set of values (e.g. `--color=always|never|auto` becomes `["always", "never", "auto"]`)
- **argument**: if this is a positional argument (not preceded by `-` or `--`), set to its name (e.g. `"FILE"`). Otherwise `null`.
- **nested_cmd**: `true` only when the argument is itself a shell command (e.g. `find -exec CMD ;`). Otherwise `false`.
- **lines**: `[start, end]` — the line range (1-indexed, inclusive) covering the flag line through the last line of its description. Use the line numbers from the Read tool output. Include ALL description lines.

Also determine:
- **dashless_opts**: `true` if the manpage documents that options can be specified without a leading dash (BSD-style, e.g. `tar xzvf`). Otherwise `false`.

Rules:
1. Extract EVERY option documented in the manpage. Do not skip any.
2. If multiple flags share one description block, include them all in the same entry.
3. Do not invent options. Only include options explicitly documented in the text.
4. Make sure line ranges cover the complete description, not just the first line.

## Step 4: Store the results

Write the extraction as JSON to a temp file and pipe it into the helper script. The JSON must follow this schema:

```json
{
  "dashless_opts": false,
  "options": [
    {
      "short": ["-f"],
      "long": ["--file"],
      "expects_arg": true,
      "argument": null,
      "nested_cmd": false,
      "lines": [42, 47]
    }
  ]
}
```

Write the JSON to `/tmp/manpage_extract.json`, then run:

```bash
source /home/idank/dev/vibe/explainshell/.venv/bin/activate && cat /tmp/manpage_extract.json | python /home/idank/dev/vibe/explainshell/tools/store_extraction.py <gz_file> --db <db_file>
```

Where `<db_file>` defaults to `/home/idank/dev/vibe/explainshell/explainshell.db`.

## Step 5: Report results

Tell the user:
- The command name extracted
- How many options were found
- Any options that were skipped due to errors
