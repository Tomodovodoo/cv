#!/usr/bin/env python3
"""Build the CV from cv.json into ../latest/ and snapshot changed versions into ../archive/.

Source of truth: cv.json. Layout: template.html + cv.css. Artwork: assets/*.svg and the portrait.
Outputs (in ../latest/): <Name>_CV.pdf, .png (300 dpi), .html (self-contained) and .md.
Normally run through ../build.cmd, which sets up the Python environment on first use.
"""
from __future__ import annotations
import argparse
import base64
import datetime as dt
import hashlib
import io
import json
import mimetypes
import re
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

GENERATOR = Path(__file__).resolve().parent
CV_ROOT = GENERATOR.parent
LATEST = CV_ROOT / 'latest'
ARCHIVE = CV_ROOT / 'archive'
SOURCES = ['cv.json', 'cv.css', 'template.html']
EXPECTED_FONTS = ['LiberationSerif', 'LiberationSans']


def output_name(cv: dict) -> str:
    return re.sub(r'\W+', '_', cv['name']).strip('_') + '_CV'


def source_fingerprint() -> str:
    digest = hashlib.sha256()
    for name in SOURCES:
        digest.update((GENERATOR / name).read_bytes())
    return digest.hexdigest()


def markdown(cv: dict) -> str:
    lines = [f"# {cv['name']}", '', f"{cv['role']} | {cv['specialism']}", '', cv['location'], '',
             ' · '.join(f"[{x['label']}]({x['url']})" for x in cv['links']), '', '## Profile', '', cv['profile'], '']
    for section in cv['sections']:
        lines += [f"## {section['title']}", '']
        for entry in section['entries']:
            title = entry['title']
            if entry.get('url'):
                title = f"[{title}]({entry['url']})"
            if entry.get('organization'):
                title += ' | ' + entry['organization']
            lines += ['### ' + title, '']
            if entry.get('date'):
                lines += [entry['date'], '']
            if entry.get('authors'):
                lines += [entry['authors'], '']
            if entry.get('publication'):
                pub = entry['publication']
                lines += [f"[{pub['label']}]({pub['url']})", '']
            if entry.get('thesis'):
                lines += ['Thesis: ' + entry['thesis'], '']
            for i, text in enumerate(entry['bullets']):
                if entry.get('bullet_urls'):
                    text = f"[{text}]({entry['bullet_urls'][i]})"
                lines.append('- ' + text)
            lines.append('')
    lines += ['## Recognition', '']
    lines += [f"- [{item['title']}]({item['url']})" if item.get('url') else f"- {item['title']}" for item in cv['recognition']]
    lines += ['', '## Languages', '', cv['languages'], '']
    return '\n'.join(lines)


def portrait_data_uri(cv: dict) -> str:
    """Return the portrait as a data URI, removing its blue backdrop if requested.

    The backdrop removal is deterministic colour matting, not generative editing. Warm skin,
    neutral fabric and the cap remain source pixels. The saturated blue backdrop becomes
    transparent; its lighter paint texture uses a slate-blue tint and variable opacity so the
    live SVG gradient shows through. Thresholds (and the iris rectangle) are tuned to the
    supplied portrait; check the output when using a different photo.
    """
    spec = cv['portrait']
    source = (GENERATOR / spec['source']).resolve()
    if not source.is_relative_to(GENERATOR):
        raise ValueError('Portrait path must remain inside the generator directory.')
    if not spec.get('remove_background'):
        mime = mimetypes.guess_type(source.name)[0]
        if mime not in ('image/png', 'image/jpeg', 'image/webp'):
            raise ValueError('Use a PNG, JPEG, or WebP portrait.')
        return f'data:{mime};base64,' + base64.b64encode(source.read_bytes()).decode('ascii')

    import numpy as np
    from PIL import Image
    with Image.open(source) as original:
        image = np.asarray(original.convert('RGBA'), dtype=np.float32)
    h, w = image.shape[:2]
    if min(h, w) < 2:
        raise ValueError('Portrait must be at least 2 by 2 pixels.')
    rgb = image[:, :, :3]
    red, green, blue = (rgb[:, :, i] for i in range(3))

    def smoothstep(low: float, high: float, values):
        t = np.clip((values - low) / (high - low), 0.0, 1.0)
        return t * t * (3.0 - 2.0 * t)

    # Saturated blue alone selects the background and paint speckles.
    # Skin and low-chroma cap/shirt pixels receive no colour modification.
    blue_selection = (smoothstep(10.0, 28.0, green - red)
                      * smoothstep(22.0, 60.0, blue - red))
    # Both blue irises are part of the subject, not the blue backdrop.
    # This rectangle lies wholly inside the face in the supplied portrait.
    blue_selection[int(0.30*h):int(0.385*h), int(0.345*w):int(0.685*w)] = 0.0
    tint = np.asarray(spec.get('texture_tint', [120, 166, 201]), dtype=np.float32)
    if tint.shape != (3,) or np.any(tint < 0) or np.any(tint > 255):
        raise ValueError('texture_tint must contain three RGB values from 0 to 255.')

    # Remove the old flat blue, retaining texture by its contrast above the base.
    texture_opacity = np.clip((green - 44.0) / 120.0, 0.0, 1.0) ** 0.9
    foreground_weight = 1.0 - blue_selection
    texture_weight = blue_selection * texture_opacity
    alpha = foreground_weight + texture_weight
    recoloured = (foreground_weight[:, :, None] * rgb
                  + texture_weight[:, :, None] * tint[None, None, :])
    recoloured /= np.maximum(alpha[:, :, None], 1e-6)

    # Feather outer artwork only. None of the facial area reaches these margins.
    x = np.arange(w, dtype=np.float32)[None, :] / (w - 1)
    y = np.arange(h, dtype=np.float32)[:, None] / (h - 1)
    feather = (smoothstep(0.0, 0.12, x) * smoothstep(0.0, 0.12, 1.0 - x)
               * smoothstep(0.0, 0.025, y) * smoothstep(0.0, 0.15, 1.0 - y))
    alpha *= feather * image[:, :, 3] / 255.0
    output = np.zeros((h, w, 4), dtype=np.uint8)
    output[:, :, :3] = np.rint(np.clip(recoloured, 0.0, 255.0)).astype(np.uint8)
    output[:, :, 3] = np.rint(np.clip(alpha, 0.0, 1.0) * 255.0).astype(np.uint8)
    buffer = io.BytesIO()
    Image.fromarray(output).save(buffer, format='PNG', optimize=True)
    return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')


def typeset(text: str):
    """Keep names and short technical phrases together; retain semantic text."""
    from markupsafe import Markup, escape
    escaped = str(escape(text))
    phrases = [
        'Magic: The Gathering', 'data lake design', 'AWS hosting',
        'Daniel Litt', '2,500 lines', '5 days', '3 days', '300,000-line',
        'The Bazaar', 'using Slurm'
    ]
    pattern = '|'.join(re.escape(str(escape(p))) for p in sorted(phrases, key=len, reverse=True))
    return Markup(re.sub(pattern, lambda m: '<span class="nowrap">' + m.group(0) + '</span>', escaped))


def write_sources(cv: dict, out_dir: Path, base: str) -> Path:
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    assets = GENERATOR / 'assets'
    icons = {p.stem: p.read_text(encoding='utf-8') for p in assets.glob('*.svg') if p.stem in ('github', 'linkedin', 'location', 'code', 'x-twitter')}
    artwork = []
    for asset in ['sidebar-background', 'orbits', 'mountains']:
        svg = (assets / f'{asset}.svg').read_text(encoding='utf-8')
        artwork.append(svg.replace('<svg ', f'<svg class="{asset}" ', 1))
    env = Environment(loader=FileSystemLoader(GENERATOR), autoescape=select_autoescape(['html']), trim_blocks=True, lstrip_blocks=True)
    env.filters['typeset'] = typeset
    html = env.get_template('template.html').render(cv=cv, css=(GENERATOR / 'cv.css').read_text(encoding='utf-8'), icons=icons,
                                                    artwork='\n'.join(artwork), portrait_data_uri=portrait_data_uri(cv))
    path = out_dir / f'{base}.html'
    path.write_text(html, encoding='utf-8')
    (out_dir / f'{base}.md').write_text(markdown(cv), encoding='utf-8')
    return path


def required_text(cv: dict) -> list[str]:
    """Headings and titles that must survive into the PDF text layer."""
    items = [cv['name']] + [s['title'] for s in cv['sections']] + [i['title'] for i in cv['recognition']]
    items += [e['title'] for s in cv['sections'] for e in s['entries']]
    return [' '.join(x.split()) for x in items]


def export(cv: dict, html_path: Path, out_dir: Path, base: str, browser_path: str | None, dpi: int) -> dict:
    import fitz
    from playwright.sync_api import sync_playwright
    pdf_path = out_dir / f'{base}.pdf'
    with sync_playwright() as pw:
        launch = {'headless': True, 'args': ['--no-sandbox', '--disable-dev-shm-usage']}
        if browser_path:
            launch['executable_path'] = browser_path
        browser = pw.chromium.launch(**launch)
        page = browser.new_page(viewport={'width': 1000, 'height': 1250}, device_scale_factor=1)
        page.set_content(html_path.read_text(encoding='utf-8'), wait_until='load')
        page.emulate_media(media='print')
        page.evaluate('document.fonts.ready')
        page.evaluate('''async () => { await Promise.all([...document.images].map(img => img.decode())); }''')
        report = page.evaluate('''() => {
          const page = document.querySelector('.page').getBoundingClientRect();
          const report = {overflow: [], links: []};
          document.querySelectorAll('.main > *, .portrait, .contact, .side-block').forEach(el => {
            const r=el.getBoundingClientRect();
            if(r.right > page.right + .8 || r.bottom > page.bottom + .8 || r.x < page.x-.8) report.overflow.push(el.id || el.className);
          });
          document.querySelectorAll('a').forEach(a=>report.links.push({text:a.innerText,url:a.href}));
          const children=[...document.querySelector('.main').children];
          for(let i=1;i<children.length;i++){
            if(children[i].getBoundingClientRect().top < children[i-1].getBoundingClientRect().bottom-.8)report.overflow.push('overlap '+children[i].id);
          }
          // Check the text itself, not only the section containers.
          const range = document.createRange();
          const walker = document.createTreeWalker(document.querySelector('.page'), NodeFilter.SHOW_TEXT);
          let textNode;
          while ((textNode = walker.nextNode())) {
            if (!textNode.textContent.trim() || textNode.parentElement.closest('svg')) continue;
            range.selectNodeContents(textNode);
            for (const r of range.getClientRects()) {
              if (!r.width || !r.height) continue;
              if (r.right > page.right + .8 || r.bottom > page.bottom + .8 || r.left < page.left - .8 || r.top < page.top - .8) {
                report.overflow.push('text: ' + textNode.textContent.trim().slice(0, 80));
              }
            }
          }
          const portrait = document.querySelector('.portrait').getBoundingClientRect();
          const contact = document.querySelector('.contact').getBoundingClientRect();
          if (portrait.bottom > contact.top + .8) report.overflow.push('portrait/contact overlap');
          const blocks = [...document.querySelectorAll('.side-block')].map(el => el.getBoundingClientRect());
          for (let i = 1; i < blocks.length; i++) if (blocks[i].top < blocks[i-1].bottom + 4) report.overflow.push('sidebar blocks overlap');
          if (!(document.querySelector('.portrait img').naturalWidth > 0)) report.overflow.push('portrait did not load');
          return report;
        }''')
        if report['overflow']:
            raise RuntimeError('Layout overflow. Shorten content or adjust spacing; the CV must fit on one page: ' + str(report['overflow']))
        page.pdf(path=str(pdf_path), format='A4', print_background=True, prefer_css_page_size=True,
                 margin={'top': '0', 'right': '0', 'bottom': '0', 'left': '0'}, display_header_footer=False, tagged=True, outline=True)
        browser.close()
    for link in report['links']:
        parsed = urlparse(link['url'])
        if parsed.scheme != 'https' or not parsed.netloc:
            raise RuntimeError('Invalid link target (must be https): ' + link['url'])
    doc = fitz.open(pdf_path)
    if len(doc) != 1:
        raise RuntimeError(f'Expected one page; got {len(doc)}. Shorten content so the CV fits on one page.')
    doc.set_metadata({'title': f"{cv['name']} | CV", 'author': cv['name'], 'subject': f"{cv['role']}, {cv['specialism']}",
                      'keywords': 'CV', 'creator': 'HTML and CSS, Chromium'})
    doc.saveIncr()
    text = doc[0].get_text()
    flat = ' '.join(text.split())
    for required in required_text(cv):
        if required not in flat:
            raise RuntimeError(f'Text missing from PDF: {required}')
    if '�' in text:
        raise RuntimeError('Replacement character found in PDF text.')
    fonts = {f[3].split('+')[-1] for f in doc[0].get_fonts()}
    missing_fonts = [f for f in EXPECTED_FONTS if not any(x.startswith(f) for x in fonts)]
    pix = doc[0].get_pixmap(dpi=dpi, alpha=False)
    pix.save(out_dir / f'{base}.png')
    doc.close()
    return {'links': len(report['links']), 'png': (pix.width, pix.height), 'missing_fonts': missing_fonts}


def archive_snapshot(base: str) -> Path | None:
    """Copy latest/ into archive/<date>/ when the sources differ from the newest snapshot."""
    fingerprint = source_fingerprint()
    snapshots = sorted(p for p in ARCHIVE.glob('*/build-info.json'))
    if snapshots:
        newest = max(snapshots, key=lambda p: json.loads(p.read_text(encoding='utf-8'))['built'])
        if json.loads(newest.read_text(encoding='utf-8'))['fingerprint'] == fingerprint:
            return None
    target = ARCHIVE / dt.date.today().isoformat()
    target.mkdir(parents=True, exist_ok=True)
    for ext in ('pdf', 'png', 'html', 'md'):
        shutil.copy2(LATEST / f'{base}.{ext}', target / f'{base}.{ext}')
    shutil.copy2(GENERATOR / 'cv.json', target / 'cv.json')
    info = {'built': dt.datetime.now().isoformat(timespec='seconds'), 'fingerprint': fingerprint}
    (target / 'build-info.json').write_text(json.dumps(info, indent=2), encoding='utf-8')
    return target


def publish(tmp_dir: Path, out_dir: Path, base: str, exts: tuple[str, ...]) -> None:
    """Copy the finished build into out_dir, refusing up front if any target file is open elsewhere."""
    for ext in exts:
        target = out_dir / f'{base}.{ext}'
        if target.exists():
            try:
                with open(target, 'r+b'):
                    pass
            except PermissionError:
                raise RuntimeError(f'{target.name} is open in another program (e.g. a PDF viewer). Close it and build again; nothing was replaced.') from None
    for ext in exts:
        shutil.copy2(tmp_dir / f'{base}.{ext}', out_dir / f'{base}.{ext}')


DOCS = CV_ROOT / 'docs'
TRACKED = [".gitignore", "README.md", "build.cmd", "publish.cmd", "generator", "latest", "docs"]
DOWNLOAD_LINK = ('<style>.pdf-download{position:fixed;top:16px;right:16px;padding:8px 14px;border-radius:6px;background:#0f2a44;'
                 'color:#fff;font:600 14px/1.2 "Liberation Sans",Arial,sans-serif;text-decoration:none;box-shadow:0 2px 10px #10263b40}'
                 '.pdf-download:hover{background:#1d5fa3;color:#fff;text-decoration:none}@media print{.pdf-download{display:none}}</style>'
                 '<a class="pdf-download" href="{pdf}" download>Download PDF</a>')


def git(*cmd: str) -> str:
    import subprocess
    done = subprocess.run(['git', '-C', str(CV_ROOT), *cmd], capture_output=True, text=True, encoding='utf-8')
    if done.returncode:
        raise RuntimeError(f"git {' '.join(cmd)} failed: {(done.stderr or done.stdout).strip()}")
    return done.stdout


def publish_site(base: str) -> None:
    """Copy latest/ into docs/ (the GitHub Pages site), then commit and push the CV repo."""
    DOCS.mkdir(exist_ok=True)
    (DOCS / '.nojekyll').write_text('', encoding='utf-8')
    for ext in ('pdf', 'png'):
        shutil.copy2(LATEST / f'{base}.{ext}', DOCS / f'{base}.{ext}')
    html = (LATEST / f'{base}.html').read_text(encoding='utf-8')
    (DOCS / 'index.html').write_text(html.replace('</body>', DOWNLOAD_LINK.replace('{pdf}', f'{base}.pdf') + '\n</body>', 1), encoding='utf-8')
    git('add', '--', *TRACKED)
    if not git('status', '--porcelain', '--', *TRACKED).strip():
        print('Online CV already up to date; nothing to publish.')
        return
    git('commit', '-q', '-m', f'Update CV ({dt.date.today().isoformat()})')
    git('push', '-q')
    print(f'Published online: https://tomodovodoo.github.io/cv/ (PDF: https://tomodovodoo.github.io/cv/{base}.pdf)')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--html-only', action='store_true', help='Update only the HTML and Markdown (no browser, no PDF).')
    parser.add_argument('--no-archive', action='store_true', help='Do not snapshot this build into archive/.')
    parser.add_argument('--browser', help='Path to Chromium or Google Chrome (default: Playwright Chromium).')
    parser.add_argument('--dpi', type=int, default=300, help='PNG resolution, default 300 dpi.')
    parser.add_argument('--output', type=Path, help='Write to this folder instead of latest/ (implies --no-archive).')
    parser.add_argument('--publish', action='store_true', help='After a successful build, update docs/ and push to GitHub (tomodovodoo.github.io/cv).')
    args = parser.parse_args()
    if args.publish and (args.output or args.html_only):
        parser.error('--publish only works for a full build into latest/.')

    cv = json.loads((GENERATOR / 'cv.json').read_text(encoding='utf-8'))
    base = output_name(cv)
    out_dir = args.output.resolve() if args.output else LATEST
    out_dir.mkdir(parents=True, exist_ok=True)
    # Build into a temporary folder so a failed build never replaces the current latest CV.
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        html_path = write_sources(cv, tmp_dir, base)
        if args.html_only:
            publish(tmp_dir, out_dir, base, ('html', 'md'))
            print(f'Built {base}.html and {base}.md in {out_dir}')
            return 0
        result = export(cv, html_path, tmp_dir, base, args.browser, args.dpi)
        publish(tmp_dir, out_dir, base, ('pdf', 'png', 'html', 'md'))

    print(f"Built one-page PDF with {result['links']} links and a {result['png'][0]} x {result['png'][1]} PNG.")
    print(f'{"Latest CV" if out_dir == LATEST else "Output"}: {out_dir / (base + ".pdf")}')
    if result['missing_fonts']:
        print(f"WARNING: fonts {', '.join(result['missing_fonts'])} are not installed; the layout uses fallbacks and may differ.")
    if not args.no_archive and out_dir == LATEST:
        snapshot = archive_snapshot(base)
        print(f'Archived this version in {snapshot}' if snapshot else 'Content unchanged since the last archived version; no new snapshot.')
    if args.publish:
        publish_site(base)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('Build failed:', exc, file=sys.stderr)
        raise SystemExit(1)
