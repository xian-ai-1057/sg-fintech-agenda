"""讀取團隊行程檔（.xlsx 或 .csv），轉成 [{'nickname', 'session_id'}] 列表。

只用標準函式庫：.xlsx 本質是 zip 包 XML，第一張工作表就夠用。
欄位依表頭名稱找：nickname（或 暱稱／name／member），session_id（或 url，
從網址抓 AGND 編號）。其餘欄位（標題、時間、場地）只是給人看的，以 agenda.csv 為準。
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
NICK_HEADERS = ('nickname', '暱稱', 'name', 'member', '成員')
SESSION_HEADERS = ('session_id', 'session', 'id', '場次編號')


def _column(ref: str) -> int:
    letters = re.match(r'[A-Z]+', ref).group()
    index = 0
    for ch in letters:
        index = index * 26 + ord(ch) - 64
    return index - 1


def _xlsx_table(data: bytes) -> list[list[str]]:
    with zipfile.ZipFile(io.BytesIO(data)) as book:
        names = book.namelist()
        shared = []
        if 'xl/sharedStrings.xml' in names:
            for item in ET.fromstring(book.read('xl/sharedStrings.xml')).findall('m:si', NS):
                shared.append(''.join(t.text or '' for t in item.iter('{%s}t' % NS['m'])))
        sheets = sorted(n for n in names if re.fullmatch(r'xl/worksheets/sheet\d+\.xml', n))
        if not sheets:
            raise ValueError('這個 .xlsx 沒有工作表。')
        sheet = 'xl/worksheets/sheet1.xml' if 'xl/worksheets/sheet1.xml' in sheets else sheets[0]
        table = []
        for row in ET.fromstring(book.read(sheet)).iter('{%s}row' % NS['m']):
            cells = {}
            for cell in row.findall('m:c', NS):
                kind, value = cell.get('t'), cell.find('m:v', NS)
                if kind == 's' and value is not None:
                    text = shared[int(value.text)]
                elif kind == 'inlineStr':
                    text = ''.join(t.text or '' for t in cell.iter('{%s}t' % NS['m']))
                else:
                    text = value.text if value is not None and value.text else ''
                cells[_column(cell.get('r', 'A1'))] = text
            if cells:
                table.append([cells.get(i, '') for i in range(max(cells) + 1)])
        return table


def rows_from_table(table: list[list[str]]) -> list[dict]:
    if not table:
        raise ValueError('檔案是空的。')
    header = [h.strip().lower() for h in table[0]]
    def find(options):
        return next((header.index(o) for o in options if o in header), None)
    nick, session, url = find(NICK_HEADERS), find(SESSION_HEADERS), find(('url', '網址'))
    if nick is None or (session is None and url is None):
        raise ValueError('找不到 nickname 與 session_id（或 url）欄位。')
    rows = []
    for line in table[1:]:
        get = lambda i: line[i].strip() if i is not None and i < len(line) else ''
        if not get(nick):
            continue
        rows.append({'nickname': get(nick), 'session_id': get(session) or get(url)})
    return rows


def read_rows(path: str | Path) -> list[dict]:
    path = Path(path)
    data = path.read_bytes()
    if path.suffix.lower() == '.xlsx' or data[:2] == b'PK':
        return rows_from_table(_xlsx_table(data))
    text = data.decode('utf-8-sig')
    return rows_from_table(list(csv.reader(io.StringIO(text))))
