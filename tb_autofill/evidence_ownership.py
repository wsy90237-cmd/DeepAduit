"""资料归属提示：仅识别明确标签，结果供人工判断。"""
from __future__ import annotations

import calendar
import re
from datetime import date

MAX_PAGES = 100
MAX_CHARACTERS = 200_000
_NAME = re.compile(r'(?<![\w])(?:银行账户户名|对账单户名|被审计单位|账户户名|开户户名|户名)\s*[:：]\s*([^\r\n；;，,]{1,100})')
_DATE = re.compile(r'(?<![\w])(?:余额日期|余额截止日|余额截至日|截止日期|截止日|截至日期|截至日)\s*[:：]?\s*(\d{4})\s*[年./-]\s*(\d{1,2})\s*[月./-]\s*(\d{1,2})\s*日?')


def _normalized(value):
    return re.sub(r'[\s（）()]+', '', value).casefold()


def check_ownership(pages, entity, year, month):
    """接收逐页字符串，返回 issues、truncated；提示不能替代人工确认。"""
    entity = str(entity).strip()
    if not entity:
        raise ValueError('必须指定当前公司名称')
    year, month = int(year), int(month)
    cutoff = date(year, month, calendar.monthrange(year, month)[1])
    issues, used, truncated = [], 0, False
    for page_no, text in enumerate(pages, 1):
        if page_no > MAX_PAGES or used >= MAX_CHARACTERS:
            truncated = True
            break
        if not isinstance(text, str):
            raise ValueError('页面内容必须是文字')
        remaining = MAX_CHARACTERS - used
        if len(text) > remaining:
            text, truncated = text[:remaining], True
        used += len(text)
        for match in _NAME.finditer(text):
            if _normalized(match.group(1)) != _normalized(entity):
                issues.append({'page': page_no, 'kind': '主体待核', 'original': match.group(0).strip(),
                               'detail': f'标注户名与当前公司“{entity}”不一致，请人工核对。'})
        for match in _DATE.finditer(text):
            try:
                found = date(*(int(x) for x in match.groups()))
            except ValueError:
                issues.append({'page': page_no, 'kind': '日期待核', 'original': match.group(0).strip(),
                               'detail': '标注日期无效，请人工核对。'})
                continue
            if found != cutoff:
                issues.append({'page': page_no, 'kind': '日期待核', 'original': match.group(0).strip(),
                               'detail': f'标注日期与账套月末 {cutoff.isoformat()} 不一致，请人工核对。'})
    return {'issues': issues, 'truncated': truncated}
