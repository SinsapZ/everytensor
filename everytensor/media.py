from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile
import threading
from html.parser import HTMLParser
from pathlib import Path

TEXT = {'.txt', '.md', '.rst', '.csv', '.log'}
CODE = {'.py', '.js', '.ts', '.tsx', '.jsx', '.c', '.cpp', '.h', '.rs', '.go', '.java', '.json', '.yaml', '.yml', '.toml', '.css', '.html', '.sql', '.sh', '.ps1'}
IMAGE = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff'}
VIDEO = {'.mp4', '.mov', '.mkv', '.avi', '.webm', '.m4v'}
AUDIO = {'.wav', '.mp3', '.m4a', '.flac', '.ogg', '.opus', '.aac', '.wma'}
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
PDF_LOCK = threading.RLock()


def executable(name):
    """Use the optional, app-local media tools without changing the user's PATH."""
    root = os.environ.get('EVERYTENSOR_COMPONENTS')
    if root:
        candidate = Path(root) / 'ffmpeg' / 'bin' / (name + '.exe')
        if candidate.is_file():
            return str(candidate)
    return shutil.which(name) or name


def kind(path):
    ext = Path(path).suffix.lower()
    if ext in CODE:
        return 'code'
    if ext in TEXT or ext in {'.pdf', '.epub', '.xlsx', '.docx', '.pptx'}:
        return 'document'
    if ext in IMAGE:
        return 'image'
    if ext in VIDEO:
        return 'video'
    if ext in AUDIO:
        return 'audio'
    return 'other'


def run(args, timeout=90):
    if args and args[0] in ('ffmpeg', 'ffprobe', 'ffplay'):
        args = [executable(args[0]), *args[1:]]
    proc = subprocess.run(args, capture_output=True, timeout=timeout, creationflags=FLAGS)
    if proc.returncode:
        raise RuntimeError(proc.stderr.decode('utf-8', 'replace')[-700:])
    return proc.stdout


def probe(path):
    data = json.loads(run(['ffprobe', '-v', 'error', '-show_entries',
                          'format=duration:stream=codec_type,width,height', '-of', 'json', str(path)]))
    return float(data['format']['duration'])


def windows(duration, size=8., step=4., start=0.):
    if duration <= 0:
        return
    while True:
        end = min(duration, start + size)
        yield start, end
        if end >= duration:
            break
        start += step


def frame(path, second=0., width=640):
    from PIL import Image
    raw = run(['ffmpeg', '-v', 'error', '-threads', '1', '-ss', str(max(0, second)),
               '-i', str(path), '-frames:v', '1', '-vf', f'scale={width}:-2',
               '-f', 'image2pipe', '-vcodec', 'mjpeg', '-threads', '1', '-'])
    with Image.open(io.BytesIO(raw)) as img:
        return img.convert('RGB')


def video_frames(path, start, end):
    # Bound memory and decode the clip once. Keep ordered frames, never a cover-only proxy.
    from PIL import Image
    import numpy as np
    width, height = 336, 224
    raw = run(['ffmpeg', '-v', 'error', '-threads', '1', '-ss', str(start), '-i', str(path),
               '-t', str(end-start), '-vf',
               f'fps=1,scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2',
               '-frames:v', '8', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-threads', '1', '-'])
    size = width*height*3
    images = [Image.fromarray(np.frombuffer(raw[i:i+size], dtype=np.uint8).reshape(height, width, 3))
              for i in range(0, len(raw)-size+1, size)]
    return images or [frame(path, start, width)]


def play(path, second=0):
    if not Path(executable('ffplay')).is_file():
        raise RuntimeError('需要 ffplay 才能从命中时间播放，请安装 FFmpeg 并加入 PATH。')
    return subprocess.Popen([executable('ffplay'), '-loglevel', 'error', '-autoexit', '-ss', str(second),
                             '-window_title', Path(path).name, '-x', '960', '-i', str(path)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=FLAGS)


def chunks(text, size=1400, overlap=180, start_offset=0):
    line = text.count('\n', 0, start_offset)+1
    for start in range(start_offset, len(text), size-overlap):
        part = text[start:start+size]
        if part.strip():
            yield part, {'offset': start, 'line': line}
        # Count each character once. Recounting the entire prefix for every
        # chunk makes large text/code files quadratic in their length.
        line += text.count('\n', start, start+size-overlap)


def digest_file(path, checkpoint=lambda: None):
    h = hashlib.sha256()
    with open(path, 'rb') as file:
        for block in iter(lambda: file.read(1024*1024), b''):
            checkpoint()
            h.update(block)
    return h.hexdigest()


class PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        if tag in ('p', 'div', 'br', 'h1', 'h2', 'li'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden-1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def archive_read(archive, name):
    info = archive.getinfo(name)
    if info.file_size > 32*1024*1024:
        raise ValueError('压缩文档单项超过 32 MiB 限制')
    return archive.read(name)


def _extract(path, resume=None):
    """Yield (content, location). No original file is ever modified."""
    path = Path(path)
    resume = resume or {}
    typ, ext = kind(path), path.suffix.lower()
    if typ in ('video', 'audio'):
        duration = probe(path)
        if typ == 'video' and resume.get('modality') != 'audio':
            for start, end in windows(duration, start=resume.get('start', 0.)):
                yield '', {'start': start, 'end': end, 'duration': duration}
        if typ == 'audio' or has_audio(path):
            for start, end in windows(duration, size=30., step=25.,
                                      start=resume.get('start', 0.) if resume.get('modality') == 'audio' else 0.):
                yield '', {'start': start, 'end': end, 'duration': duration, 'modality': 'audio'}
    elif typ == 'image':
        yield '', {'image': True}
    elif ext == '.pdf':
        import fitz
        with PDF_LOCK:
            doc = fitz.open(path)
        try:
            with PDF_LOCK:
                needs_pass, page_count = doc.needs_pass, len(doc)
            if needs_pass:
                raise ValueError('加密 PDF 需要密码')
            for num in range(max(0, int(resume.get('page', 1))-1), page_count):
                with PDF_LOCK:
                    text = doc[num].get_text(sort=True)
                    illustrated = bool(doc[num].get_images())
                from .content_quality import assess, annotate
                assessment = assess(text)
                if text.strip():
                    for part, loc in chunks(text):
                        yield part, annotate(text, dict(loc, page=num+1))
                if assessment.visual_fallback or (illustrated and len(text.strip()) < 120):
                    yield '', {'page': num+1, 'page_image': True}
        finally:
            with PDF_LOCK:
                doc.close()
    elif ext == '.epub':
        import posixpath
        with zipfile.ZipFile(path) as archive:
            container = ET.fromstring(archive_read(archive, 'META-INF/container.xml'))
            opf = next(x.attrib['full-path'] for x in container.iter() if x.tag.endswith('rootfile'))
            package = ET.fromstring(archive_read(archive, opf))
            manifest = {x.attrib['id']: x.attrib['href'] for x in package.iter() if x.tag.endswith('}item')}
            spine = [x.attrib['idref'] for x in package.iter() if x.tag.endswith('}itemref')]
            from urllib.parse import unquote
            for chapter, ref in enumerate(spine, 1):
                if chapter < resume.get('chapter', 1):
                    continue
                name = posixpath.normpath(posixpath.join(posixpath.dirname(opf), unquote(manifest[ref])))
                parser = PlainHTML()
                parser.feed(archive_read(archive, name).decode('utf-8', 'replace'))
                for part, loc in chunks(''.join(parser.parts)):
                    yield part, dict(loc, chapter=chapter)
    elif ext == '.xlsx':
        with zipfile.ZipFile(path) as archive:
            ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
            shared = []
            if 'xl/sharedStrings.xml' in archive.namelist():
                root = ET.fromstring(archive_read(archive, 'xl/sharedStrings.xml'))
                shared = [''.join(x.itertext()) for x in root.findall('s:si', ns)]
            workbook = ET.fromstring(archive_read(archive, 'xl/workbook.xml'))
            rels = ET.fromstring(archive_read(archive, 'xl/_rels/workbook.xml.rels'))
            targets = {x.attrib['Id']: x.attrib['Target'] for x in rels}
            import posixpath
            for sheet in workbook.findall('s:sheets/s:sheet', ns):
                rel = sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']
                target = targets[rel]
                name = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/'+target)
                root = ET.fromstring(archive_read(archive, name))
                for row in root.findall('.//s:row', ns):
                    cells = []
                    for cell in row.findall('s:c', ns):
                        value = cell.findtext('s:v', '', ns)
                        if cell.get('t') == 's' and value:
                            value = shared[int(value)]
                        elif cell.get('t') == 'inlineStr':
                            value = ''.join(cell.find('s:is', ns).itertext())
                        if value:
                            cells.append(f"{cell.get('r')}: {value}")
                    for part, loc in chunks(' | '.join(cells)):
                        yield part, dict(loc, sheet=sheet.attrib['name'], row=int(row.get('r', 0)))
    elif ext in ('.docx', '.pptx'):
        from .office import extract_docx, extract_pptx
        yield from (extract_docx(path) if ext == '.docx' else extract_pptx(path))
    elif typ in ('document', 'code'):
        if path.stat().st_size > 32*1024*1024:
            raise ValueError('文本文件超过 32 MiB，当前仅索引文件名')
        raw = path.read_bytes()
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = raw.decode('gb18030', 'replace')
        yield from chunks(text, start_offset=resume.get('offset', 0))


def extract(path, *, resume=None):
    """Resume at an exact saved locator; PDF/media skip earlier pages/windows."""
    waiting = bool(resume)
    source = _extract(path, resume)
    try:
        for text, loc in source:
            if waiting:
                if loc != resume:
                    continue
                waiting = False
            yield text, loc
        if waiting:
            raise ValueError('无法恢复文件提取位置；源文件可能已改变')
    finally:
        source.close()


def preview_image(path, loc, bound=(720, 700)):
    from PIL import Image, ImageOps
    if Path(path).suffix.lower() == '.pdf':
        import fitz
        with PDF_LOCK, fitz.open(path) as doc:
            page = doc[max(0, min(len(doc)-1, loc.get('page', 1)-1))]
            scale = min(bound[0]/page.rect.width, bound[1]/page.rect.height)
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            return Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
    if kind(path) == 'video':
        img = frame(path, loc.get('start', 0), bound[0])
        img.thumbnail(bound)
        return img
    with Image.open(path) as original:
        img = ImageOps.exif_transpose(original).convert('RGB')
        size = img.size
        img.thumbnail(bound)
        img.info['original_size'] = size
        return img


def timestamp(seconds):
    seconds = int(seconds)
    return f'{seconds//3600:02}:{seconds//60%60:02}:{seconds%60:02}' if seconds >= 3600 else f'{seconds//60:02}:{seconds%60:02}'


def has_audio(path):
    data = json.loads(run(['ffprobe', '-v', 'error', '-select_streams', 'a:0',
                          '-show_entries', 'stream=codec_type', '-of', 'json', str(path)]))
    return bool(data.get('streams'))


def audio_samples(path, start, end):
    """Decode a bounded window; never load the complete recording into memory."""
    import numpy as np
    start, end = max(0., float(start)), float(end)
    if not 0 < end-start <= 30.001:
        raise ValueError('音频窗口必须在 0–30 秒之间')
    data = run(['ffmpeg', '-v', 'error', '-ss', str(start), '-i', str(path),
                '-t', str(end-start), '-map', '0:a:0', '-vn', '-ac', '1',
                '-ar', '16000', '-f', 'f32le', 'pipe:1'])
    values = np.frombuffer(data, dtype='<f4').copy()
    if not len(values) or not np.isfinite(values).all():
        raise ValueError('没有可解码的音频内容')
    return {'array': values, 'sampling_rate': 16000}
