import io
import warnings
import zipfile
from pathlib import Path
import openpyxl
import xlrd
from xml.etree.ElementTree import ParseError
from openpyxl.utils.exceptions import InvalidFileException
from xlrd.compdoc import CompDocError


def read_sheets(content, filename, max_rows=30000):
    try:
        return _read_sheets(content, filename, max_rows)
    except (zipfile.BadZipFile, InvalidFileException, xlrd.XLRDError, CompDocError, ParseError, KeyError, EOFError, IndexError) as exc:
        raise ValueError("Excel 文件损坏、内容不完整或缺少必要结构，请从来源重新导出后重试") from exc


def _read_sheets(content, filename, max_rows):
    suffix = Path(filename).suffix.lower()
    if suffix not in (".xls", ".xlsx"):
        raise ValueError("请选择 .xls 或 .xlsx 文件")
    sheets = []
    if content.startswith(b"PK"):
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if sum(i.file_size for i in archive.infolist()) > 120 * 1024 * 1024:
                raise ValueError("Excel 解压后超过 120 MB，请缩小文件")
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Workbook contains no default style.*")
            workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        try:
            for sheet in workbook:
                # 部分导出文件错误声明 A1:A1，必须重新读取实际范围。
                sheet.reset_dimensions()
                rows = []
                for row in sheet.iter_rows(values_only=True):
                    if len(rows) >= max_rows:
                        raise ValueError(f"工作表“{sheet.title}”超过 {max_rows:,} 行上限（含标题、空行和合计）；整次导入已停止，未截取前部分数据")
                    if len(row) > 300:
                        raise ValueError(f"工作表“{sheet.title}”超过 300 列上限，整次导入已停止")
                    rows.append(list(row))
                sheets.append({"name": sheet.title, "rows": rows})
        finally:
            workbook.close()
    elif content.startswith(bytes.fromhex("d0cf11e0a1b11ae1")):
        workbook = xlrd.open_workbook(file_contents=content)
        try:
            for sheet in workbook.sheets():
                if sheet.nrows > max_rows:
                    raise ValueError(f"工作表“{sheet.name}”超过 {max_rows:,} 行上限（含标题、空行和合计）；整次导入已停止，未截取前部分数据")
                if sheet.ncols > 300:
                    raise ValueError(f"工作表“{sheet.name}”超过 300 列上限，整次导入已停止")
                rows = []
                for index in range(sheet.nrows):
                    rows.append([
                        xlrd.xldate.xldate_as_datetime(cell.value, workbook.datemode)
                        if cell.ctype == xlrd.XL_CELL_DATE else cell.value
                        for cell in sheet.row(index)
                    ])
                sheets.append({"name": sheet.name, "rows": rows})
        finally:
            workbook.release_resources()
    else:
        raise ValueError("文件内容不是支持的 Excel 格式；请另存为标准 .xls 或 .xlsx")
    return sheets
