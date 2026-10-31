"""Read OOXML in its declared order, without Office automation or extraction."""
import posixpath
import xml.etree.ElementTree as ET
import zipfile

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
P = '{http://schemas.openxmlformats.org/presentationml/2006/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'


def read(archive, name):
    from .media import archive_read
    return ET.fromstring(archive_read(archive, name))


def related(archive, part):
    folder, filename = posixpath.split(part)
    name = posixpath.join(folder, '_rels', filename + '.rels')
    if name not in archive.namelist():
        return {}
    return {node.attrib['Id']: (node.attrib.get('Type', ''),
            posixpath.normpath(posixpath.join(folder, node.attrib['Target']))
            if not node.attrib['Target'].startswith('/') else node.attrib['Target'].lstrip('/'))
            for node in read(archive, name) if node.attrib.get('TargetMode') != 'External'}


def word_text(node):
    return ''.join((n.text or '') if n.tag == W+'t' else '\t' if n.tag == W+'tab' else '\n'
                   for n in node.iter() if n.tag in (W+'t', W+'tab', W+'br', W+'cr'))


def extract_docx(path):
    from .media import chunks
    with zipfile.ZipFile(path) as archive:
        body = read(archive, 'word/document.xml').find(W+'body')
        if body is None:
            return
        paragraph, table = 0, 0
        for block in body:
            if block.tag == W+'p':
                paragraph += 1
                for text, loc in chunks(word_text(block)):
                    yield text, dict(loc, paragraph=paragraph, part='body')
            elif block.tag == W+'tbl':
                table += 1
                for number, row in enumerate(block.findall(W+'tr'), 1):
                    cells = ['\n'.join(word_text(p) for p in cell.iter(W+'p')) for cell in row.findall(W+'tc')]
                    for text, loc in chunks(' | '.join(cells)):
                        yield text, dict(loc, table=table, row=number, part='table')
                    paragraph += sum(1 for _ in row.iter(W+'p'))


def drawing_text(node):
    return '\n'.join(''.join(n.text or '' for n in paragraph.iter(A+'t')) for paragraph in node.iter(A+'p'))


def extract_pptx(path):
    from .media import chunks
    with zipfile.ZipFile(path) as archive:
        relations = related(archive, 'ppt/presentation.xml')
        presentation = read(archive, 'ppt/presentation.xml')
        for number, slide in enumerate(presentation.iter(P+'sldId'), 1):
            _, part = relations[slide.attrib[R+'id']]
            for text, loc in chunks(drawing_text(read(archive, part))):
                yield text, dict(loc, slide=number, part='slide')
            for rel_type, note in related(archive, part).values():
                if rel_type.endswith('/notesSlide'):
                    root = read(archive, note)
                    paragraphs = []
                    for shape in root.iter(P+'sp'):
                        placeholder = shape.find('.//'+P+'ph')
                        if placeholder is None or placeholder.attrib.get('type') not in ('sldNum', 'hdr', 'ftr', 'dt'):
                            paragraphs.append(drawing_text(shape))
                    for text, loc in chunks('\n'.join(paragraphs)):
                        yield text, dict(loc, slide=number, part='notes')
