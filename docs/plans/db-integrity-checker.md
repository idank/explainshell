# DB Integrity Checker

## Problem

Issues like the original `ps` shadowing bug (multiple manpages with the same name+section+distro creating ambiguous mappings) are only discovered through confusing log output. There's no proactive way to detect database integrity problems.

## Goal

A tool/command that scans the database and reports:

- **Shadowed duplicates**: multiple manpages with the same name+section+distro that create ambiguous mappings
- **Orphaned mappings**: mapping rows that reference non-existent manpage IDs
- **Other inconsistencies**: missing required fields, malformed source paths, etc.

## Approach

Add a `--check` mode to `explainshell.manager` (or a standalone script) that connects to the DB, runs the checks, and prints a report. Should be runnable as part of CI or after imports.
