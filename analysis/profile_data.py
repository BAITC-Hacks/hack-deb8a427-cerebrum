"""Read-only CSV profiling; Python standard library, no evaluator imports.

Run with --help for explicit mappings. No identifiers or source rows are emitted.
Missing data/mappings produce NOT_CHECKED, never a successful validation.
"""

import argparse
from collections import Counter
import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
from itertools import combinations
from pathlib import Path
import re
import sys


SEGMENTS = ("arpu_segment", "data_segment", "call_segment")


def cell(value):
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def numeric(value):
    try:
        number = Decimal(value)
        return number if number.is_finite() else None
    except InvalidOperation:
        return None


@dataclass
class Dataset:
    name: str
    sha256: str
    columns: list
    rows: list

    def values(self, column):
        index = self.columns.index(column)
        return [row[index] for row in self.rows]


def read_dataset(path, root, encoding, delimiter):
    """Retain CSV strings exactly, including leading zeroes and whitespace."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    with path.open(encoding=encoding, newline="") as stream:
        reader = csv.reader(stream, delimiter=delimiter, strict=True)
        columns = next(reader, None)
        if not columns or any(not name.strip() for name in columns):
            raise ValueError("missing or empty column names")
        if len(set(columns)) != len(columns):
            raise ValueError("duplicate column names")
        rows = []
        for row in reader:
            if len(row) != len(columns):
                raise ValueError(f"wrong field count at CSV line {reader.line_num}")
            rows.append(tuple(row))
    return Dataset(path.relative_to(root).as_posix(), digest.hexdigest(), columns, rows)


def column_stats(values, is_identifier=False):
    present = [value for value in values if value.strip()]
    numbers = [numeric(value) for value in present]
    finite = [value for value in numbers if value is not None]
    if is_identifier:
        kind = "string (identifier)"
    elif not present:
        kind = "empty"
    elif all(re.fullmatch(r"[+-]?\d+", value.strip()) for value in present):
        kind = "integer"
    elif len(finite) == len(present):
        kind = "decimal"
    else:
        kind = "string/mixed"
    return {
        "type": kind,
        "missing": len(values) - len(present),
        "numeric": len(finite),
        "min": str(min(finite)) if finite and not is_identifier else "n/a",
        "max": str(max(finite)) if finite and not is_identifier else "n/a",
    }


def identifier_like(column):
    """Suppress numeric ranges for obvious keys; not a semantic schema mapping."""
    tokens = set(re.split(r"[^a-z0-9]+", column.lower()))
    return bool(tokens & {"id", "msisdn", "imsi", "imei", "phone"})


def distribution(lines, dataset, column):
    counts = Counter(dataset.values(column))
    lines.extend([f"### {cell(column)}", "", "| Значение | Строк |", "| --- | ---: |"])
    for value, count in sorted(counts.items()):
        lines.append(f"| {cell(value) if value.strip() else '(пусто)'} | {count} |")
    lines.append("")


def build_report(args):
    root = args.data_dir.resolve()
    lines = ["# Профиль данных", "", "Данные кейса синтетические. Выводы о реальном бизнесе Beeline не делаются.", ""]
    checks = []

    def check(status, message):
        checks.append((status, message))

    datasets = {}
    inputs = sorted(root.rglob("*.csv")) if root.is_dir() else []
    output = args.output.resolve() if args.output else None
    for path in inputs:
        # Ignore Git metadata and refuse to follow links outside the input folder.
        if ".git" in path.relative_to(root).parts:
            continue
        if not path.resolve().is_relative_to(root) or path.resolve() == output:
            check("ERROR", "Недопустимый путь CSV или совпадение входа и выхода.")
            continue
        try:
            dataset = read_dataset(path, root, args.encoding, args.delimiter)
            datasets[dataset.name] = dataset
        except (OSError, UnicodeError, csv.Error, ValueError) as error:
            # Do not print exception payloads which may contain source cell values.
            check("ERROR", f"Не удалось прочитать {path.relative_to(root).as_posix()} ({type(error).__name__}).")
    if not datasets:
        check("NOT_CHECKED", "В указанной папке нет доступных CSV участников; числовой профиль не построен.")

    def resolve(reference):
        """A reference is an explicit relative/path.csv:COLUMN, never guessed."""
        name, separator, column = reference.rpartition(":")
        name = name.replace("\\", "/")
        if not separator or name not in datasets or column not in datasets[name].columns:
            raise ValueError("unknown file/column reference")
        return datasets[name], column

    id_refs = []
    for reference in args.id_column:
        try:
            pair = resolve(reference)
            if pair not in id_refs:
                id_refs.append(pair)
        except ValueError:
            check("NOT_CHECKED", f"Не найдено сопоставление идентификатора: {reference}.")
    profile = datasets.get(args.customer_profile)
    if not profile:
        check("NOT_CHECKED", "customer_profile не найден; распределения и проверки аудитории недоступны.")

    special_ids = {(dataset.name, column) for dataset, column in id_refs}
    if profile:
        for column in (args.customer_id, args.current_tariff):
            if column:
                special_ids.add((profile.name, column))
    tariff_ref = None
    if args.tariff_key:
        try:
            tariff_ref = resolve(args.tariff_key)
            special_ids.add((tariff_ref[0].name, tariff_ref[1]))
        except ValueError:
            check("NOT_CHECKED", "Указанный ключ словаря тарифов не найден.")

    lines.extend(["## Методика", "",
                  "CSV читаются без изменения исходников. Пропуск — пустая строка или только пробелы; "
                  "литералы NA/null не считаются пропусками автоматически. Полный дубль — повтор всей "
                  "строки по исходным строковым значениям (счёт сверх первого вхождения).",
                  "Типы выведены из всех непустых значений, а не из схемы CSV. Диапазон — min/max "
                  "конечных чисел; для смешанных колонок это только числовая часть. "
                  "Идентификаторы сравниваются как строки, без удаления пробелов и ведущих нулей.",
                  f"Кодировка: `{cell(args.encoding)}`; разделитель: `{cell(repr(args.delimiter))}`. "
                  "В отчёте нет строк исходных данных и значений идентификаторов абонентов.", "",
                  "## Датасеты", ""])
    for dataset in datasets.values():
        if not dataset.rows:
            check("ISSUE", f"Датасет {dataset.name} не содержит строк.")
        lines.extend([f"### {cell(dataset.name)}", "", f"SHA-256: `{dataset.sha256}`", "",
                      f"Строк: **{len(dataset.rows)}**; колонок: **{len(dataset.columns)}**; "
                      f"полных дублей: **{len(dataset.rows) - len(set(dataset.rows))}**.", "",
                      "| Колонка | Тип | Пропуски | Числовых значений | Min | Max |",
                      "| --- | --- | ---: | ---: | --- | --- |"])
        for column in dataset.columns:
            stats = column_stats(dataset.values(column), (dataset.name, column) in special_ids or identifier_like(column))
            lines.append("| " + " | ".join(cell(value) for value in [column, *stats.values()]) + " |")
        lines.append("")

    if profile:
        lines.extend(["## Аудитория customer_profile", ""])
        if args.customer_id and args.customer_id in profile.columns:
            values = profile.values(args.customer_id)
            counts = Counter(value for value in values if value.strip())
            blanks = sum(not value.strip() for value in values)
            repeated = sum(count - 1 for count in counts.values())
            lines.extend([f"Уникальных непустых идентификаторов абонентов: **{len(counts)}**. "
                          f"Пустых: **{blanks}**; повторных строк по идентификатору: **{repeated}**.", ""])
            check("ISSUE" if blanks or repeated else "OK", "Ключ абонента: "
                  f"пустых={blanks}, повторных строк={repeated}.")
        else:
            check("NOT_CHECKED", "Не указан или отсутствует столбец идентификатора абонента (--customer-id).")
        for column in (*SEGMENTS, args.current_tariff):
            if column and column in profile.columns:
                distribution(lines, profile, column)
            else:
                check("NOT_CHECKED", f"Недоступно распределение: {column or 'текущий тариф (--current-tariff)'}.")
        if "predicted_arpu" in profile.columns:
            values = profile.values("predicted_arpu")
            stats = column_stats(values)
            invalid = len(values) - stats["missing"] - stats["numeric"]
            lines.extend([f"predicted_arpu: min={stats['min']}, max={stats['max']}; "
                          f"пропусков={stats['missing']}, нечисловых/неконечных={invalid}.", ""])
            check("ISSUE" if invalid or stats["missing"] else "OK", "Числовая полнота predicted_arpu.")
        else:
            check("NOT_CHECKED", "Нет predicted_arpu.")

    if profile and args.current_tariff in profile.columns and tariff_ref:
        dictionary, column = tariff_ref
        keys = dictionary.values(column)
        valid = {value for value in keys if value.strip()}
        values = profile.values(args.current_tariff)
        blanks = sum(not value.strip() for value in values)
        unknown = sum(bool(value.strip()) and value not in valid for value in values)
        dictionary_blanks = sum(not value.strip() for value in keys)
        duplicates = len(keys) - dictionary_blanks - len(valid)
        check("ISSUE" if blanks or unknown or dictionary_blanks or duplicates else "OK",
              f"Тарифы: пустых в профиле={blanks}; строк с неизвестным тарифом={unknown}; "
              f"пустых ключей словаря={dictionary_blanks}; повторных ключей словаря={duplicates}.")
    else:
        check("NOT_CHECKED", "Ссылочная целостность тарифов требует --current-tariff и --tariff-key файл.csv:колонка.")

    if len({dataset.name for dataset, _ in id_refs}) < 2:
        check("NOT_CHECKED", "Сопоставление идентификаторов между файлами требует минимум двух --id-column файл.csv:колонка.")
    for (left, lc), (right, rc) in combinations(id_refs, 2):
        if left.name == right.name:
            continue
        a = {value for value in left.values(lc) if value.strip()}
        b = {value for value in right.values(rc) if value.strip()}
        check("INFO", f"Идентификаторы {left.name}:{lc} ↔ {right.name}:{rc}: "
              f"общих={len(a & b)}, только слева={len(a - b)}, только справа={len(b - a)}. "
              "Различие выборок само по себе не является ошибкой; назначение связи нужно сверить со словарём.")

    # Rules are supplied as exact references to dictionary columns, not guessed
    # from the observed audience. Each referenced column lists allowed values.
    rules = {}
    for rule in args.segment_rule:
        segment, separator, reference = rule.partition("=")
        if not separator or segment not in SEGMENTS or segment in rules:
            check("ERROR", "Некорректное или повторное сопоставление --segment-rule.")
            continue
        try:
            rules[segment] = resolve(reference)
        except ValueError:
            check("NOT_CHECKED", f"Не найден столбец словаря для {segment}.")
    for segment in SEGMENTS:
        if profile and segment in profile.columns and segment in rules:
            dictionary, column = rules[segment]
            allowed = {value for value in dictionary.values(column) if value.strip()}
            if not allowed:
                check("NOT_CHECKED", f"Словарь {segment} не содержит допустимых значений.")
                continue
            values = profile.values(segment)
            blanks = sum(not value.strip() for value in values)
            unknown = sum(bool(value.strip()) and value not in allowed for value in values)
            check("ISSUE" if blanks or unknown else "OK", f"{segment} по {dictionary.name}:{column}: "
                  f"пустых={blanks}; строк вне словаря={unknown}.")
        else:
            check("NOT_CHECKED", f"Для {segment} нет явного сопоставления со словарём допустимых значений.")

    lines.extend(["## Проверки", "", "| Статус | Результат |", "| --- | --- |"])
    lines.extend(f"| {status} | {cell(message)} |" for status, message in checks)
    lines.extend(["", "## Значение для алгоритма", "",
                  "- Пропуски и повторные ключи нужно учесть до объединения таблиц: дубли могут увеличивать оценку аудитории.",
                  "- Размеры категорий помогают проверить представимость сегментов; размер конкретной кампании нужно считать по пересечению её фильтров.",
                  "- Распределение ARPU не определяет эффект смены тарифа; для выбора кампаний необходимы пилоты.",
                  "- NOT_CHECKED означает отсутствие данных или сопоставления, а не успешную проверку.", ""])
    incomplete = any(status in {"ERROR", "NOT_CHECKED"} for status, _ in checks)
    issues = any(status == "ISSUE" for status, _ in checks)
    return "\n".join(lines), 2 if incomplete else 1 if issues else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True, help="Folder containing ONLY participant CSVs/dictionaries")
    parser.add_argument("--customer-profile", default="customer_profile.csv", help="Relative CSV path")
    parser.add_argument("--customer-id", help="Exact customer identifier column; no schema assumption")
    parser.add_argument("--current-tariff", help="Exact current tariff column")
    parser.add_argument("--tariff-key", help="Dictionary reference: relative/path.csv:COLUMN")
    parser.add_argument("--id-column", action="append", default=[], help="Repeat for equivalent customer ID columns: file.csv:COLUMN")
    parser.add_argument("--segment-rule", action="append", default=[], help="SEGMENT=file.csv:COLUMN listing documented allowed values")
    parser.add_argument("--encoding", default="utf-8-sig")
    parser.add_argument("--delimiter", default=",")
    parser.add_argument("--output", type=Path, help="Markdown output; stdout if omitted")
    args = parser.parse_args(argv)
    if len(args.delimiter) != 1 or args.delimiter in "\r\n":
        parser.error("--delimiter must be one non-newline character")
    if args.output and args.output.suffix.lower() != ".md":
        parser.error("--output must have .md extension; CSV output is not permitted")
    report, status = build_report(args)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8", newline="\n")
    else:
        print(report)
    return status


if __name__ == "__main__":
    sys.exit(main())
