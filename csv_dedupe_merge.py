#!/usr/bin/env python3
"""Merge CSV files in input order, retaining the first row for each key."""

import argparse
import csv
import json
import os
import sys
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path


class MergeError(Exception):
    """A data or configuration error suitable for display to the user."""


def read_header(reader):
    try:
        header = next(reader)
    except StopIteration:
        raise MergeError("missing header") from None
    if not header or "" in header:
        raise MergeError("empty header or unnamed column")
    if len(set(header)) != len(header):
        raise MergeError("column names must be unique")
    return header


@contextmanager
def csv_input(path):
    # newline="" lets csv handle CR, LF, CRLF, and quoted multiline fields.
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream, strict=True)
        try:
            yield reader
        except (MergeError, csv.Error, UnicodeError) as exc:
            raise MergeError(
                f"{path}: near physical line {max(1, reader.line_num)}: {exc}"
            ) from exc


def plan_schema(paths, allow_ragged):
    headers = []
    columns = {}  # Insertion order is the output column order.
    for path in paths:
        with csv_input(path) as reader:
            header = read_header(reader)
        if headers and not allow_ragged and header != headers[0]:
            raise MergeError(f"{path}: header differs from {paths[0]}")
        headers.append(header)
        columns.update(dict.fromkeys(header))
    return headers, list(columns)


def key_positions(key_arg, schema):
    if key_arg is None:
        return list(range(len(schema)))
    names = key_arg.split(",")
    if "" in names or len(set(names)) != len(names):
        raise MergeError("--key requires distinct, nonempty column names")
    positions = {name: i for i, name in enumerate(schema)}
    for name in names:
        if name not in positions:
            raise MergeError(f"unknown --key column: {name!r}")
    return [positions[name] for name in names]


def check_destinations(inputs, destinations):
    for path in inputs:
        if not path.is_file():
            raise MergeError(f"{path}: input is not a regular file")
    reserved = list(inputs)
    for dest in destinations:
        if dest.is_symlink():
            raise MergeError(f"{dest}: refusing to replace a symbolic link")
        exists = dest.exists()
        if exists and not dest.is_file():
            raise MergeError(f"{dest}: destination is not a regular file")
        for other in reserved:
            if dest.resolve() == other.resolve() or (
                exists and other.exists() and dest.samefile(other)
            ):
                raise MergeError(f"{dest}: destination conflicts with {other}")
        reserved.append(dest)


def cleanup(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def stage(stack, destination):
    stream = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="", dir=destination.parent,
        prefix=".csv-dedupe-", suffix=".tmp", delete=False,
    )
    stack.callback(cleanup, stream.name)
    return stream


def write_merge(args, headers, schema, positions, output):
    writer = csv.writer(output)
    writer.writerow(schema)
    seen = set()
    files = []
    removed_total = 0
    schema_index = {name: i for i, name in enumerate(schema)}

    for path, expected in zip(args.inputs, headers):
        rows = removed = 0
        mapping = [schema_index[name] for name in expected]
        aligned = expected == schema
        with csv_input(path) as reader:
            if read_header(reader) != expected:
                raise MergeError("header changed during merge")
            for values in reader:
                if not values:  # Blank physical record, not a quoted empty field.
                    continue
                rows += 1
                width = len(expected)
                if len(values) > width or (len(values) < width and not args.allow_ragged):
                    raise MergeError(f"expected {width} fields, found {len(values)}")
                if aligned:
                    row = values
                    row.extend([""] * (width - len(row)))
                else:
                    row = [""] * len(schema)
                    for i, value in zip(mapping, values):
                        row[i] = value

                if args.fuzzy:
                    key = tuple(" ".join(row[i].casefold().split()) for i in positions)
                else:
                    key = tuple(row[i] for i in positions)
                if key in seen:
                    removed += 1
                else:
                    seen.add(key)
                    writer.writerow(row)
        files.append({
            "path": str(path), "rows": rows, "duplicates_removed": removed,
            "rows_kept": rows - removed,
        })
        removed_total += removed
    return {
        "files": files, "duplicates_removed": removed_total, "final_count": len(seen),
    }


def merge(args):
    if len(args.inputs) < 2:
        raise MergeError("at least two input CSV files are required")
    destinations = [args.output]
    if args.report is not None:
        destinations.append(args.report)
    check_destinations(args.inputs, destinations)
    headers, schema = plan_schema(args.inputs, args.allow_ragged)
    positions = key_positions(args.key, schema)

    # Close all staged streams before replacement (required on Windows).
    with ExitStack() as stack:
        with stage(stack, args.output) as output:
            report = write_merge(args, headers, schema, positions, output)
        if args.report is not None:
            with stage(stack, args.report) as report_file:
                json.dump(report, report_file, indent=2, ensure_ascii=True)
                report_file.write("\n")
        os.replace(output.name, args.output)
        if args.report is not None:
            try:
                os.replace(report_file.name, args.report)
            except OSError as exc:
                raise MergeError(f"CSV written, but report replacement failed: {exc}") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("inputs", nargs="+", type=Path, metavar="CSV",
                        help="two or more UTF-8 input files, in precedence order")
    parser.add_argument("-o", "--output", required=True, type=Path, metavar="FILE",
                        help="destination CSV (replaces an existing regular file)")
    parser.add_argument("--report", type=Path, metavar="FILE",
                        help="write JSON counts to this destination")
    parser.add_argument("--key", metavar="COL[,COL...]",
                        help="exact column names to compare (default: all)")
    parser.add_argument("--fuzzy", action="store_true",
                        help="casefold and normalize whitespace in comparison keys")
    parser.add_argument("--allow-ragged", action="store_true",
                        help="align by column name and pad absent fields with empty strings")
    args = parser.parse_args(argv)
    try:
        merge(args)
    except (MergeError, OSError, RuntimeError, csv.Error, UnicodeError) as exc:
        parser.exit(1, f"{parser.prog}: error: {exc}\n")
    except KeyboardInterrupt:
        parser.exit(130, f"{parser.prog}: interrupted\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
