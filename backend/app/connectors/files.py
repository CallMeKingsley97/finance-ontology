"""CSV / Excel 文件连接器：解析表头、推断类型、统计行数与样本。"""

from __future__ import annotations

import csv
import io
import re
import time
import zipfile
from datetime import date, datetime, time as time_value
from pathlib import Path
from typing import Any, Iterable, Iterator

from app.connectors.base import (
    ColumnMeta,
    ConnectorError,
    MetadataSnapshot,
    TableMeta,
    TestResult,
    infer_text_type,
    json_safe,
)

_TYPE_INFER_ROWS = 1000
_SAMPLE_ROWS = 5
_HEADER_MAX_LENGTH = 128
_MAX_SHEETS = 50
_MAX_SHEET_COLUMNS = 200
_MAX_CSV_COLUMNS = 1000
_MAX_EXCEL_UNCOMPRESSED = 200 * 1024 * 1024
_MAX_COMPRESSION_RATIO = 100
_NAME_PATTERN = re.compile(r"[^A-Za-z0-9_\-一-鿿]")
_NUMBER_PATTERN = re.compile(r"^[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?$")


def _unique_table_name(stem: str, fallback: str) -> str:
    cleaned = _NAME_PATTERN.sub("_", stem).strip("_")
    return (cleaned or fallback)[:_HEADER_MAX_LENGTH]


def _text_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time_value):
        return value.isoformat()
    return str(value)


def _looks_numeric(value: str) -> bool:
    text = value.strip()
    return bool(text) and bool(_NUMBER_PATTERN.match(text))


def _detect_header(row: list[Any]) -> bool:
    cells = [_text_value(cell).strip() for cell in row]
    if not cells or any(not cell for cell in cells):
        return False
    if len({cell.lower() for cell in cells}) != len(cells):
        return False
    return not all(_looks_numeric(cell) for cell in cells)


def _clean_header(row: list[Any], width: int) -> list[str]:
    cleaned: list[str] = []
    used: set[str] = set()
    for index in range(width):
        raw = row[index] if index < len(row) else None
        name = _text_value(raw).strip() or f"column_{index + 1}"
        name = name[:_HEADER_MAX_LENGTH]
        base = name
        suffix = 2
        while name.lower() in used:
            suffix_text = f"_{suffix}"
            name = base[: _HEADER_MAX_LENGTH - len(suffix_text)] + suffix_text
            suffix += 1
        used.add(name.lower())
        cleaned.append(name)
    return cleaned


def _default_columns(width: int) -> list[str]:
    return [f"column_{index + 1}" for index in range(width)]


def _read_text_sample(path: Path) -> tuple[str, str]:
    """读取前 64KB 并识别编码；拒绝二进制内容。"""
    with path.open("rb") as handle:
        raw = handle.read(65536)
    if b"\x00" in raw:
        raise ConnectorError("CSV 文件包含二进制内容")
    if not raw:
        return "", "utf-8-sig"
    window = raw[: raw.rfind(b"\n") + 1] if b"\n" in raw else raw
    try:
        return window.decode("utf-8-sig"), "utf-8-sig"
    except UnicodeDecodeError:
        pass
    try:
        from charset_normalizer import from_bytes

        # 中文财务数据以 UTF-8 / GB18030 为主；限制候选可避免短样本被误判为 big5 等旧编码。
        best = from_bytes(raw, cp_isolation=["utf_8", "gb18030"]).best()
        if best is not None:
            text = str(best)
            if text and "�" not in text:
                return text, best.encoding or "gb18030"
    except Exception:
        pass
    try:
        return raw.decode("gb18030"), "gb18030"
    except UnicodeDecodeError as exc:
        raise ConnectorError("CSV 编码识别失败") from exc


def _detect_delimiter(sample: str) -> str:
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        return ","
    return dialect.delimiter or ","


class CsvConnector:
    """CSV 文件连接器：单表、schema=file。"""

    kind = "csv"

    def __init__(self, path: Path, config: dict[str, Any] | None = None) -> None:
        self.path = Path(path)
        self.config = config or {}
        self.encoding = ""
        self.delimiter = ","
        self.has_header = False
        self._sample_rows: dict[str, list[dict[str, Any]]] = {}

    def _require_file(self) -> None:
        if not self.path.is_file():
            raise ConnectorError("CSV 文件不存在")

    def test(self) -> TestResult:
        started = time.perf_counter()
        snapshot = self.inspect()
        return TestResult(
            success=True,
            latency_ms=int((time.perf_counter() - started) * 1000),
            table_count=len(snapshot.tables),
            message="CSV 文件可解析",
        )

    def inspect(self) -> MetadataSnapshot:
        self._require_file()
        sample_text, self.encoding = _read_text_sample(self.path)
        self.delimiter = _detect_delimiter(sample_text)
        warnings: list[str] = []
        display_name = str(self.config.get("file_name") or self.path.name)
        table_name = _unique_table_name(Path(display_name).stem, "csv_data")

        with self.path.open("r", encoding=self.encoding, newline="", errors="strict") as handle:
            reader = csv.reader(handle, delimiter=self.delimiter)
            first_row: list[str] = []
            for row in reader:
                if any(_text_value(cell).strip() for cell in row):
                    first_row = row
                    break
            if not first_row:
                warnings.append("CSV 文件为空")
                return MetadataSnapshot(
                    kind=self.kind,
                    server_version=None,
                    tables=[
                        TableMeta(
                            schema="file",
                            name=table_name,
                            table_type="file",
                            row_count=0,
                            columns=[],
                        )
                    ],
                    truncated=False,
                    warnings=warnings,
                )

            self.has_header = _detect_header(first_row)
            raw_width = len(first_row)
            width = min(raw_width, _MAX_CSV_COLUMNS)
            if raw_width > width:
                warnings.append(f"仅解析前 {_MAX_CSV_COLUMNS} 列")
            columns = _clean_header(first_row, width) if self.has_header else _default_columns(width)
            type_values: list[list[str]] = [[] for _ in range(width)]
            samples: list[dict[str, Any]] = []
            row_count = 0

            def consume(row: list[str]) -> None:
                nonlocal row_count
                if row_count < _TYPE_INFER_ROWS:
                    for index in range(width):
                        value = row[index] if index < len(row) else ""
                        type_values[index].append(_text_value(value))
                if row_count < _SAMPLE_ROWS:
                    samples.append(
                        {
                            columns[index]: json_safe(row[index] if index < len(row) else None)
                            for index in range(width)
                        }
                    )
                row_count += 1

            if not self.has_header:
                consume(first_row)
            for row in reader:
                if not any(_text_value(cell).strip() for cell in row):
                    continue
                consume(row)

        table = TableMeta(
            schema="file",
            name=table_name,
            table_type="file",
            row_count=row_count,
            row_count_estimated=False,
            comment=f"CSV 文件 {self.path.name}",
            columns=[
                ColumnMeta(name=columns[index], data_type=infer_text_type(type_values[index]))
                for index in range(width)
            ],
        )
        self._sample_rows[table_name] = samples
        return MetadataSnapshot(
            kind=self.kind,
            server_version=None,
            tables=[table],
            truncated=bool(warnings),
            warnings=warnings,
        )

    def sample_rows(self, table: TableMeta, limit: int = 5) -> list[dict[str, Any]]:
        if not self._sample_rows:
            self.inspect()
        safe_limit = max(1, min(int(limit or _SAMPLE_ROWS), _SAMPLE_ROWS))
        return self._sample_rows.get(table.name, [])[:safe_limit]

    def iter_rows(
        self,
        table: TableMeta,
        *,
        watermark_column: str | None = None,
        watermark_value: str | None = None,
        batch_size: int = 1000,
        max_rows: int = 200_000,
    ) -> Iterator[list[dict[str, Any]]]:
        """按 inspect 相同的编码/分隔符/表头规则逐批读取 CSV 行。"""
        self._require_file()
        if not self.encoding:
            self.inspect()
        encoding = self.encoding or "utf-8-sig"
        delimiter = self.delimiter or ","
        names = [column.name for column in table.columns]
        if not names:
            return
        safe_batch = max(1, int(batch_size or 1000))
        safe_max = max(1, int(max_rows or 1))
        width = len(names)

        def emit(values: list[Any]) -> dict[str, Any]:
            return {
                name: json_safe(values[index] if index < len(values) else None)
                for index, name in enumerate(names)
            }

        with self.path.open("r", encoding=encoding, newline="", errors="strict") as handle:
            reader = csv.reader(handle, delimiter=delimiter)
            first_row: list[str] = []
            for row in reader:
                if any(_text_value(cell).strip() for cell in row):
                    first_row = row
                    break
            if not first_row:
                return
            has_header = _detect_header(first_row)
            batch: list[dict[str, Any]] = []
            yielded = 0
            if not has_header:
                batch.append(emit(first_row))
                yielded += 1
            if yielded < safe_max:
                for row in reader:
                    if not any(_text_value(cell).strip() for cell in row):
                        continue
                    batch.append(emit(row))
                    yielded += 1
                    if len(batch) >= safe_batch:
                        yield batch
                        batch = []
                    if yielded >= safe_max:
                        break
            if batch:
                yield batch

    def close(self) -> None:
        """文件解析按需读取，无需额外清理。"""


def _check_excel_archive(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
    except (zipfile.BadZipFile, OSError) as exc:
        raise ConnectorError("Excel 文件不是有效的 xlsx 压缩包") from exc
    total_size = sum(info.file_size for info in infos)
    total_compressed = sum(info.compress_size for info in infos)
    if total_size > _MAX_EXCEL_UNCOMPRESSED:
        raise ConnectorError("UPLOAD_SUSPICIOUS_ARCHIVE: 解压后体积超过限制")
    if total_compressed > 0 and total_size / total_compressed > _MAX_COMPRESSION_RATIO:
        raise ConnectorError("UPLOAD_SUSPICIOUS_ARCHIVE: 压缩比超过限制")


class ExcelConnector:
    """Excel 文件连接器：仅 .xlsx/.xlsm，data_only 只读缓存值。"""

    kind = "excel"

    def __init__(self, path: Path, config: dict[str, Any] | None = None) -> None:
        self.path = Path(path)
        self.config = config or {}
        self._sample_rows: dict[str, list[dict[str, Any]]] = {}

    def _load_workbook(self):
        suffix = self.path.suffix.lower()
        if suffix == ".xls":
            raise ConnectorError("EXCEL_LEGACY_UNSUPPORTED: 不支持 .xls，请另存为 .xlsx")
        if suffix not in (".xlsx", ".xlsm"):
            raise ConnectorError("仅支持 .xlsx/.xlsm 文件")
        if not self.path.is_file():
            raise ConnectorError("Excel 文件不存在")
        _check_excel_archive(self.path)
        try:
            import openpyxl
        except ImportError as exc:
            raise ConnectorError("Excel 解析依赖未安装") from exc
        try:
            return openpyxl.load_workbook(
                filename=str(self.path),
                read_only=True,
                data_only=True,
            )
        except Exception as exc:
            raise ConnectorError(f"Excel 文件解析失败: {exc}") from exc

    def test(self) -> TestResult:
        started = time.perf_counter()
        snapshot = self.inspect()
        return TestResult(
            success=True,
            latency_ms=int((time.perf_counter() - started) * 1000),
            table_count=len(snapshot.tables),
            message="Excel 文件可解析",
        )

    def inspect(self) -> MetadataSnapshot:
        workbook = self._load_workbook()
        warnings: list[str] = ["Excel 行数可能受格式影响"]
        tables: list[TableMeta] = []
        try:
            sheets = list(workbook.worksheets)
            if len(sheets) > _MAX_SHEETS:
                warnings.append(f"仅解析前 {_MAX_SHEETS} 个工作表")
                sheets = sheets[:_MAX_SHEETS]
            for worksheet in sheets:
                table = self._inspect_sheet(worksheet, warnings)
                if table is not None:
                    tables.append(table)
        finally:
            workbook.close()
        return MetadataSnapshot(
            kind=self.kind,
            server_version=None,
            tables=tables,
            truncated=len(warnings) > 1,
            warnings=warnings,
        )

    def _inspect_sheet(self, worksheet, warnings: list[str]) -> TableMeta | None:
        rows: Iterable[tuple[Any, ...]] = worksheet.iter_rows(values_only=True)
        first_row: list[Any] = []
        for row in rows:
            values = list(row)
            if any(_text_value(value).strip() for value in values):
                first_row = values
                break
        if not first_row:
            return None

        has_header = _detect_header(first_row)
        width = min(len(first_row), _MAX_SHEET_COLUMNS)
        if len(first_row) > _MAX_SHEET_COLUMNS:
            warnings.append(f"工作表 {worksheet.title} 仅展示前 {_MAX_SHEET_COLUMNS} 列")
        columns = _clean_header(first_row, width) if has_header else _default_columns(width)
        type_values: list[list[str]] = [[] for _ in range(width)]
        samples: list[dict[str, Any]] = []
        row_count = 0

        def consume(row: list[Any]) -> None:
            nonlocal row_count
            if row_count < _TYPE_INFER_ROWS:
                for index in range(width):
                    value = row[index] if index < len(row) else None
                    type_values[index].append(_text_value(value))
            if row_count < _SAMPLE_ROWS:
                samples.append(
                    {
                        columns[index]: json_safe(row[index] if index < len(row) else None)
                        for index in range(width)
                    }
                )
            row_count += 1

        if not has_header:
            consume(first_row)
        for row in rows:
            values = list(row)
            if not any(_text_value(value).strip() for value in values):
                continue
            consume(values)

        name = worksheet.title[:_HEADER_MAX_LENGTH] or "sheet"
        table = TableMeta(
            schema="excel",
            name=name,
            table_type="file",
            row_count=row_count,
            row_count_estimated=True,
            comment=f"工作表 {worksheet.title}",
            columns=[
                ColumnMeta(name=columns[index], data_type=infer_text_type(type_values[index]))
                for index in range(width)
            ],
        )
        self._sample_rows[name] = samples
        return table

    def sample_rows(self, table: TableMeta, limit: int = 5) -> list[dict[str, Any]]:
        if not self._sample_rows:
            self.inspect()
        safe_limit = max(1, min(int(limit or _SAMPLE_ROWS), _SAMPLE_ROWS))
        return self._sample_rows.get(table.name, [])[:safe_limit]

    def iter_rows(
        self,
        table: TableMeta,
        *,
        watermark_column: str | None = None,
        watermark_value: str | None = None,
        batch_size: int = 1000,
        max_rows: int = 200_000,
    ) -> Iterator[list[dict[str, Any]]]:
        """按工作表标题匹配表名，逐批只读读取缓存值。"""
        names = [column.name for column in table.columns]
        if not names:
            return
        safe_batch = max(1, int(batch_size or 1000))
        safe_max = max(1, int(max_rows or 1))
        workbook = self._load_workbook()
        try:
            worksheet = next(
                (sheet for sheet in workbook.worksheets if sheet.title == table.name),
                None,
            )
            if worksheet is None:
                raise ConnectorError(f"工作表不存在: {table.name}")

            def emit(values: list[Any]) -> dict[str, Any]:
                return {
                    name: json_safe(values[index] if index < len(values) else None)
                    for index, name in enumerate(names)
                }

            rows = worksheet.iter_rows(values_only=True)
            first_row: list[Any] = []
            for row in rows:
                values = list(row)
                if any(_text_value(value).strip() for value in values):
                    first_row = values
                    break
            if not first_row:
                return
            has_header = _detect_header(first_row)
            batch: list[dict[str, Any]] = []
            yielded = 0
            if not has_header:
                batch.append(emit(first_row))
                yielded += 1
            if yielded < safe_max:
                for row in rows:
                    values = list(row)
                    if not any(_text_value(value).strip() for value in values):
                        continue
                    batch.append(emit(values))
                    yielded += 1
                    if len(batch) >= safe_batch:
                        yield batch
                        batch = []
                    if yielded >= safe_max:
                        break
            if batch:
                yield batch
        finally:
            workbook.close()

    def close(self) -> None:
        """工作簿在 inspect 内关闭，无需额外清理。"""
