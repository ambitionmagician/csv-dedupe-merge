# CSV Dedupe Merge

Merge two or more CSVs in command-line order. The first row with each comparison key wins, even if a later row has different non-key values. Fuzzy matching affects only the comparison: the output retains the winning row's field values.

## Install

Save the script as `csv_dedupe_merge.py`. It requires Python 3.7+ and only the standard library.

## Usage

    python csv_dedupe_merge.py january.csv february.csv -o combined.csv
    python csv_dedupe_merge.py primary.csv backup.csv -o contacts.csv --key email --fuzzy --report counts.json
    python csv_dedupe_merge.py old.csv new.csv corrections.csv -o ledger.csv --allow-ragged --key account,date
    python csv_dedupe_merge.py --help

Without `--key`, all output columns form the key. `--fuzzy` casefolds each key field, trims surrounding whitespace, and collapses each internal whitespace run to one space. It does not perform typo or similarity matching.

By default, headers must match exactly, including column order, and each row must have the declared width. `--allow-ragged` forms a union of column names in first-seen order, aligns rows by name, and fills missing columns and trailing fields with empty strings. Undeclared extra fields are still errors. Missing and explicitly empty fields compare equally.

The optional JSON report lists each input's data-row count, duplicates removed, and rows kept, followed by the overall duplicates removed and final row count. A duplicate is charged to the file containing the discarded occurrence.

## Format and limits

- Inputs must be regular, comma-delimited UTF-8 files, optionally with a BOM, with standard double-quote CSV quoting. Mixed line endings and quoted multiline values are supported. Output is UTF-8 without a BOM, with CRLF record separators.
- Each file must start with a header of unique, nonempty names. Names are case-sensitive and are not trimmed. A name containing a comma cannot be selected individually with `--key`. Header-only files are valid. Blank records after the header are skipped; a quoted empty field is data.
- Headers are inspected before the streaming merge; do not modify inputs during the run. Memory use grows with the total size of distinct keys. The Python CSV reader's default field-size limit applies.
- Destination directories must already exist. Existing regular output files are replaced, not appended to. Outputs cannot be inputs, symbolic links, or aliases of each other. Temporary disk space is required for the staged results. Each replacement is atomic, but the CSV and report are not replaced as a single transaction.
