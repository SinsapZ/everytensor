"""Small antialiased desktop assets, rendered from geometry and cached in memory."""
from functools import lru_cache
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter


def rounded_bitmap(image, radius):
    result = image.convert('RGBA')
    mask = surface(image.width, image.height, '#ffffff', '', radius).getchannel('A')
    result.putalpha(mask)
    return result


@lru_cache(maxsize=16)
def surface(width, height, fill, outline, radius, shadow=False):
    scale = 3
    w, h = max(1, int(width)), max(1, int(height))
    if not shadow and w > radius*2+4 and h > radius*2+4:
        # Antialias only the corners, rather than supersampling an entire
        # multi-megapixel panel on each native Configure event.
        image = Image.new('RGBA', (w, h))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((1, 1, w-1, h-1), radius, fill=fill or None, outline=outline or None)
        edge = int(radius)+2
        patch = Image.new('RGBA', (edge*6, edge*6))
        ImageDraw.Draw(patch).rounded_rectangle((3, 3, edge*6-3, edge*6-3), radius*3,
                                                 fill=fill or None, outline=outline or None, width=3)
        corner = patch.crop((0, 0, edge*3, edge*3)).resize((edge, edge), Image.Resampling.LANCZOS)
        image.paste(corner, (0, 0))
        image.paste(corner.transpose(Image.Transpose.FLIP_LEFT_RIGHT), (w-edge, 0))
        image.paste(corner.transpose(Image.Transpose.FLIP_TOP_BOTTOM), (0, h-edge))
        image.paste(corner.transpose(Image.Transpose.ROTATE_180), (w-edge, h-edge))
        return image
    image = Image.new('RGBA', (w*scale, h*scale))
    box = (scale, scale, (w-1)*scale, (h-(5 if shadow else 1))*scale)
    if shadow:
        mask = Image.new('RGBA', image.size)
        ImageDraw.Draw(mask).rounded_rectangle((box[0], box[1]+3*scale, box[2], box[3]+3*scale),
                                               radius*scale, fill=(30, 25, 25, 28))
        image = mask.filter(ImageFilter.GaussianBlur(3*scale))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(box, radius*scale, fill=fill or None, outline=outline or None, width=scale)
    return image.resize((w, h), Image.Resampling.LANCZOS)


@lru_cache(maxsize=64)
def icon(name, size=24, color='#77777c'):
    s = 3
    image = Image.new('RGBA', (size*s, size*s))
    draw = ImageDraw.Draw(image)
    def points(coords):
        return [(x*size*s/24, y*size*s/24) for x, y in coords]
    def line(coords, width=1.5):
        draw.line(points(coords), fill=color, width=max(1, int(width*s*size/24)), joint='curve')
    def ellipse(box, fill=None, outline=color, width=1.5):
        draw.ellipse(tuple(v*size*s/24 for v in box), fill=fill, outline=outline, width=max(1, int(width*s*size/24)))
    if name == 'search':
        ellipse((4, 3, 17, 16))
        line([(15, 15), (21, 21)])
    elif name == 'close':
        line([(7, 7), (17, 17)], 1.2)
        line([(17, 7), (7, 17)], 1.2)
    elif name == 'settings':
        coords = []
        for i in range(48):
            angle = i*math.pi/24
            r = 9 if i % 6 in (1, 2, 3, 4) else 7
            coords.append((12+r*math.cos(angle), 12+r*math.sin(angle)))
        line(coords+[coords[0]], 1.3)
        ellipse((8.5, 8.5, 15.5, 15.5), width=1.3)
    elif name == 'folder':
        line([(3, 19), (3, 6), (10, 6), (12, 8), (21, 8), (21, 19), (3, 19)], 1.4)
    elif name == 'external':
        line([(10, 5), (4, 5), (4, 20), (19, 20), (19, 14)], 1.4)
        line([(13, 3), (21, 3), (21, 11)], 1.4)
        line([(11, 13), (21, 3)], 1.4)
    elif name == 'fit':
        for coords in [[(8, 3), (3, 3), (3, 8)], [(16, 3), (21, 3), (21, 8)],
                       [(3, 16), (3, 21), (8, 21)], [(16, 21), (21, 21), (21, 16)]]:
            line(coords)
    elif name == 'play':
        draw.polygon(points([(8, 5), (20, 12), (8, 19)]), fill=color)
    elif name == 'video':
        draw.rounded_rectangle(tuple(v*size*s/24 for v in (2, 4, 22, 20)), 3*s, fill=color)
        draw.polygon(points([(9, 8), (16, 12), (9, 16)]), fill='white')
    elif name == 'chevron':
        line([(9, 6), (15, 12), (9, 18)], 1.2)
    elif name == 'code':
        line([(8, 6), (2, 12), (8, 18)], 2)
        line([(16, 6), (22, 12), (16, 18)], 2)
        line([(14, 3), (10, 21)], 1.7)
    elif name == 'image':
        draw.rounded_rectangle(tuple(v*size*s/24 for v in (2, 3, 22, 21)), 2*s, fill=color)
        draw.polygon(points([(4, 18), (10, 10), (14, 15), (17, 12), (21, 18)]), fill='white')
        ellipse((16, 6, 18, 8), fill='white', outline='white')
    else:
        draw.polygon(points([(5, 2), (15, 2), (21, 8), (21, 22), (5, 22)]), fill=color)
        draw.polygon(points([(15, 2), (15, 8), (21, 8)]), fill='#ffffff70')
        line_color = color
        color = 'white'
        if name == 'pdf':
            line([(8, 16), (13, 7), (11, 18), (17, 14), (8, 16)], .9)
        else:
            for y in (11, 14, 17):
                line([(8, y), (17, y)], .8)
        color = line_color
    return image.resize((size, size), Image.Resampling.LANCZOS)


@lru_cache(maxsize=8)
def brand(size):
    # These exact renders keep NumPy/native-library startup off the first frame.
    # The procedural source below remains available for new sizes and rebuilding
    # assets; missing assets retain the original rendering behavior.
    path = Path(__file__).with_name('assets')/f'brand-{size}.png'
    if path.is_file():
        with Image.open(path) as image:
            return image.convert('RGB')
    return _render_brand(size)


def _render_brand(size):
    import numpy as np
    s = size*3
    yy, xx = np.mgrid[0:s, 0:s].astype(np.float32)/s
    rgb = np.ones((s, s, 3), dtype=np.float32)*.985
    for cx, cy, radius, base in ((.39, .36, .33, (.72, .18, .14)), (.63, .60, .33, (.14, .13, .13))):
        dx, dy = (xx-cx)/radius, (yy-cy)/radius
        d = dx*dx+dy*dy
        mask = np.clip((1-d)*s/2, 0, 1)
        # Opposing glass gradients with a multiply intersection.
        shade = np.clip(.52+.37*dx+.18*dy, .12, .92) if cx < .5 else np.clip(.66-.40*dx-.24*dy, .1, .96)
        color = 1-(1-np.array(base)[None, None, :])*shade[:, :, None]
        alpha = mask[:, :, None]
        rgb *= 1-alpha+color*alpha
    return Image.fromarray((np.clip(rgb, 0, 1)*255).astype('uint8')).resize((size, size), Image.Resampling.LANCZOS)
