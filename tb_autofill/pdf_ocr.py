"""Read PDF text by page, using Windows OCR for pages without a text layer."""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
from pathlib import Path

from pypdf import PdfReader


class PdfExtractionError(ValueError):
    """The PDF could not be read in full."""


def _page_text(page) -> tuple[str, bool]:
    resources = page.get("/Resources")
    images = resources.get_object().get("/XObject") if resources else None
    images = images.get_object() if images else {}
    area = float(page.mediabox.width * page.mediabox.height)
    has_large_image = False

    def inspect_operand(operator, operands, matrix, _text_matrix):
        nonlocal has_large_image
        if operator == b"Do" and operands:
            image = images.get(operands[0])
            a, b, c, d, *_ = matrix
            if image and image.get_object().get("/Subtype") == "/Image" and abs(a * d - b * c) >= area * 0.55:
                has_large_image = True

    text = (page.extract_text(visitor_operand_before=inspect_operand) or "").strip()
    return text, not text or (has_large_image and len(re.sub(r"\s+", "", text)) <= 36)


def extract_pdf_pages(path: Path) -> list[tuple[int, str]]:
    path = Path(path)
    try:
        with path.open("rb") as source:
            reader = PdfReader(source)
            if reader.is_encrypted and not reader.decrypt(""):
                raise PdfExtractionError("PDF 受密码保护，请先解密。")
            extracted = [_page_text(page) for page in reader.pages]
    except PdfExtractionError:
        raise
    except Exception as exc:
        raise PdfExtractionError("无法读取 PDF，请检查文件是否完整。") from exc

    pages = [text for text, _ in extracted]
    scanned = [number for number, (_, requires_ocr) in enumerate(extracted, 1) if requires_ocr]
    if scanned:
        recognized = _recognize_scans(path, scanned)
        for number, text in zip(scanned, recognized, strict=True):
            pages[number - 1] = text or pages[number - 1]
    return list(enumerate(pages, 1))


def _recognize_scans(path: Path, numbers: list[int]) -> list[str]:
    if os.name != "nt":
        raise PdfExtractionError("扫描 PDF 需要 Windows 10 或 11 的本地 OCR。")

    powershell = Path(os.environ.get("SystemRoot", "C:/Windows"))
    powershell /= "System32/WindowsPowerShell/v1.0/powershell.exe"
    encoded_script = base64.b64encode(_OCR_SCRIPT.encode("utf-16-le")).decode("ascii")
    # ASCII JSON preserves all Unicode paths without depending on the active console code page.
    request = json.dumps({"path": str(path.resolve()), "pages": numbers})
    try:
        process = subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded_script],
            input=request.encode("ascii"),
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=max(60, 10 * len(numbers)),
        )
        if process.returncode == 2:
            raise PdfExtractionError("Windows 未安装可用的 OCR 语言，请在系统语言设置中安装相应语言的光学字符识别组件。")
        process.check_returncode()
        result = json.loads(process.stdout.decode("utf-8-sig"))
    except subprocess.TimeoutExpired as exc:
        raise PdfExtractionError("本地 OCR 处理超时，请拆分 PDF 后重试。") from exc
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        if isinstance(exc, PdfExtractionError):
            raise
        raise PdfExtractionError("Windows 本地 OCR 无法处理扫描页，请检查系统 OCR 组件或重新导出 PDF。") from exc
    if not isinstance(result, list) or len(result) != len(numbers) or not all(isinstance(text, str) for text in result):
        raise PdfExtractionError("本地 OCR 返回的扫描页与 PDF 页码不一致。")
    # WinRT inserts spaces between recognized Chinese characters on the same line.
    return [re.sub(r"(?<=[\u3400-\u9fff]) +(?=[\u3400-\u9fff])", "", text) for text in result]


_OCR_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$request = ConvertFrom-Json -InputObject ([Console]::In.ReadToEnd())
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
$null = [Windows.Data.Pdf.PdfDocument,Windows.Data.Pdf,ContentType=WindowsRuntime]
$null = [Windows.Data.Pdf.PdfPageRenderOptions,Windows.Data.Pdf,ContentType=WindowsRuntime]
$null = [Windows.Storage.Streams.InMemoryRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrResult,Windows.Foundation,ContentType=WindowsRuntime]

$methods = [System.WindowsRuntimeSystemExtensions].GetMethods()
$asTaskResult = $methods | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and
    $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
}
$asTaskAction = $methods | Where-Object {
    $_.Name -eq 'AsTask' -and -not $_.IsGenericMethod -and
    $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction'
}
function Wait-Result($operation, [Type]$resultType) {
    $task = $asTaskResult.MakeGenericMethod($resultType).Invoke($null, @($operation))
    return $task.GetAwaiter().GetResult()
}

$engine = $null
$languages = [Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages
$chinese = $languages | Where-Object { $_.LanguageTag -like 'zh-Hans*' -or $_.LanguageTag -eq 'zh-CN' } | Select-Object -First 1
if ($null -ne $chinese) {
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($chinese)
}
if ($null -eq $engine) {
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
}
if ($null -eq $engine) {
    $fallback = $languages | Select-Object -First 1
    if ($null -ne $fallback) {
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($fallback)
    }
}
if ($null -eq $engine) { exit 2 }

$file = Wait-Result ([Windows.Storage.StorageFile]::GetFileFromPathAsync($request.path)) ([Windows.Storage.StorageFile])
$pdf = Wait-Result ([Windows.Data.Pdf.PdfDocument]::LoadFromFileAsync($file)) ([Windows.Data.Pdf.PdfDocument])
$texts = foreach ($number in $request.pages) {
    $page = $pdf.GetPage([uint32]($number - 1))
    $stream = [Windows.Storage.Streams.InMemoryRandomAccessStream]::new()
    try {
        $options = [Windows.Data.Pdf.PdfPageRenderOptions]::new()
        $maxDimension = [Windows.Media.Ocr.OcrEngine]::MaxImageDimension
        $scale = [Math]::Min(3, $maxDimension / [Math]::Max($page.Size.Width, $page.Size.Height))
        $options.DestinationWidth = [uint32][Math]::Max(1, [Math]::Floor($page.Size.Width * $scale))
        $options.DestinationHeight = [uint32][Math]::Max(1, [Math]::Floor($page.Size.Height * $scale))
        $renderTask = $asTaskAction.Invoke($null, @($page.RenderToStreamAsync($stream, $options)))
        $null = $renderTask.GetAwaiter().GetResult()
        $stream.Seek(0)
        $decoder = Wait-Result ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap = Wait-Result ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        try {
            $result = Wait-Result ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
            ($result.Lines | ForEach-Object { $_.Text }) -join "`n"
        } finally {
            $bitmap.Dispose()
        }
    } finally {
        $stream.Dispose()
        $page.Dispose()
    }
}
[Console]::Write((ConvertTo-Json -Compress -InputObject @($texts)))
"""
