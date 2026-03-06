#!/usr/bin/env python3
"""
Obsidian to Docsify Converter

Converts Obsidian-flavored Markdown to standard Markdown compatible with Docsify.
Handles:
  - Image references: ![[image.jpg|600]] → ![image.jpg](images/image.jpg ':size=600x')
  - Image references without size: ![[image.jpg]] → ![image.jpg](images/image.jpg)
  - Obsidian internal links: [[Note Title]] → [Note Title](Note%20Title.md)
  - Auto-generates _sidebar.md for Docsify navigation
  - Copies images from source to output directory
"""

import os
import re
import shutil
import sys
import argparse
import urllib.parse
from pathlib import Path

# ── Obsidian syntax patterns ──────────────────────────────────────────────────

# ![[filename.ext|width]]  or  ![[filename.ext]]
OBSIDIAN_IMAGE_RE = re.compile(
    r'!\[\[([^\]|]+?)(?:\|(\d+))?\]\]'
)

# [[Note Title]]  or  [[Note Title|Display Text]]
OBSIDIAN_LINK_RE = re.compile(
    r'(?<!!)\[\[([^\]|]+?)(?:\|([^\]]+?))?\]\]'
)

# Common image extensions
IMAGE_EXTENSIONS = {
    '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.svg', '.webp', '.ico', '.tiff',
}


def is_image_file(filename: str) -> bool:
    """Check whether a filename looks like an image."""
    return Path(filename).suffix.lower() in IMAGE_EXTENSIONS


def convert_obsidian_images(content: str, images_rel_dir: str = "images") -> str:
    """
    Convert Obsidian image embeds to Docsify-compatible Markdown.

    ![[photo.jpg|600]]  →  ![photo.jpg](images/photo.jpg ':size=600x')
    ![[photo.jpg]]      →  ![photo.jpg](images/photo.jpg)

    Uses Docsify's ':size' attribute so that relativePath resolution works
    correctly (raw <img> tags bypass Docsify's path rewriting).
    """
    def _replace_image(match: re.Match) -> str:
        filename = match.group(1).strip()
        width = match.group(2)  # may be None

        # URL-encode the filename to handle spaces, Chinese chars, parentheses
        encoded_name = urllib.parse.quote(filename, safe='')
        src = f"{images_rel_dir}/{encoded_name}"

        if width:
            # Docsify size hint: ':size=WIDTHx' (trailing x = auto height)
            return f'![{filename}]({src} \':size={width}x\')'
        else:
            return f'![{filename}]({src})'

    return OBSIDIAN_IMAGE_RE.sub(_replace_image, content)


def convert_obsidian_links(content: str) -> str:
    """
    Convert Obsidian wikilinks to standard Markdown links.

    [[Note Title]]            →  [Note Title](Note%20Title.md)
    [[Note Title|Display]]    →  [Display](Note%20Title.md)
    """
    def _replace_link(match: re.Match) -> str:
        target = match.group(1).strip()
        display = match.group(2)

        # Skip if the target looks like an image
        if is_image_file(target):
            return match.group(0)

        encoded_target = urllib.parse.quote(target, safe='/')
        # Add .md extension if it doesn't already have one
        if not encoded_target.lower().endswith('.md'):
            encoded_target += '.md'

        label = display.strip() if display else target
        return f'[{label}]({encoded_target})'

    return OBSIDIAN_LINK_RE.sub(_replace_link, content)


def convert_file(content: str, images_rel_dir: str = "images") -> str:
    """Apply all Obsidian → standard Markdown conversions."""
    content = convert_obsidian_images(content, images_rel_dir)
    content = convert_obsidian_links(content)
    return content


# ── Sidebar generation ────────────────────────────────────────────────────────

def build_sidebar(src_root: Path) -> str:
    """
    Walk *src_root* and produce a Docsify ``_sidebar.md``.

    The sidebar lists directories as section headers and .md files as links,
    sorted alphabetically, skipping README.md (used as landing page).
    """
    lines: list[str] = []

    # Collect top-level .md files (not README)
    top_files = sorted(
        f for f in src_root.iterdir()
        if f.is_file() and f.suffix.lower() == '.md' and f.name.lower() != 'readme.md'
    )
    for f in top_files:
        title = _title_from_filename(f)
        lines.append(f'- [{title}](/{f.name})')

    # Collect sub-directories (skip hidden, images, scripts, .github, _site)
    skip_dirs = {'images', 'scripts', '.github', '_site', '.git', '.obsidian', 'node_modules'}
    subdirs = sorted(
        d for d in src_root.iterdir()
        if d.is_dir() and d.name not in skip_dirs and not d.name.startswith('.')
    )

    for d in subdirs:
        lines.append(f'- **{d.name}**')
        _walk_dir_for_sidebar(d, d.name, lines, depth=1, skip_dirs=skip_dirs)

    return '\n'.join(lines) + '\n'


def _walk_dir_for_sidebar(
    directory: Path,
    rel_prefix: str,
    lines: list[str],
    depth: int,
    skip_dirs: set[str],
) -> None:
    """Recursively add entries for a directory."""
    indent = '  ' * depth

    # .md files in this directory
    md_files = sorted(
        f for f in directory.iterdir()
        if f.is_file() and f.suffix.lower() == '.md' and f.name.lower() != 'readme.md'
    )
    for f in md_files:
        title = _title_from_filename(f)
        link = f'/{rel_prefix}/{f.name}'
        lines.append(f'{indent}- [{title}]({link})')

    # Recurse into sub-directories
    child_dirs = sorted(
        d for d in directory.iterdir()
        if d.is_dir() and d.name not in skip_dirs and not d.name.startswith('.')
    )
    for d in child_dirs:
        lines.append(f'{indent}- **{d.name}**')
        _walk_dir_for_sidebar(d, f'{rel_prefix}/{d.name}', lines, depth + 1, skip_dirs=skip_dirs)


def _title_from_filename(filepath: Path) -> str:
    """Derive a human-readable title from a markdown filename."""
    return filepath.stem.replace('_', ' ').replace('-', ' ')


# ── Main build logic ─────────────────────────────────────────────────────────

def build_site(src_dir: str, out_dir: str) -> None:
    """
    Build the Docsify site.

    1. Copy every file from *src_dir* → *out_dir*.
    2. For every .md file, run the Obsidian → Markdown converter.
    3. Generate ``_sidebar.md`` in *out_dir*.
    """
    src = Path(src_dir).resolve()
    out = Path(out_dir).resolve()

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    skip_dirs = {'.git', '.github', '.obsidian', 'node_modules', '_site', 'scripts'}

    for root, dirs, files in os.walk(src):
        # Skip hidden / unwanted directories
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith('.')]

        rel_root = Path(root).relative_to(src)
        dest_root = out / rel_root
        dest_root.mkdir(parents=True, exist_ok=True)

        for fname in files:
            src_file = Path(root) / fname
            dst_file = dest_root / fname

            if fname.lower().endswith('.md'):
                # Convert and write
                content = src_file.read_text(encoding='utf-8')

                # Determine relative path to the images/ directory
                images_rel = 'images'
                converted = convert_file(content, images_rel)
                dst_file.write_text(converted, encoding='utf-8')
                print(f'  converted: {rel_root / fname}')
            else:
                # Copy as-is (images, html, css, etc.)
                shutil.copy2(src_file, dst_file)

    # Generate sidebar
    sidebar_content = build_sidebar(out)
    (out / '_sidebar.md').write_text(sidebar_content, encoding='utf-8')
    print(f'  generated: _sidebar.md')

    # Create .nojekyll to prevent GitHub Pages from ignoring _sidebar.md
    (out / '.nojekyll').write_text('', encoding='utf-8')
    print(f'  generated: .nojekyll')

    print(f'\nBuild complete → {out}')


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description='Convert Obsidian notes to a Docsify-compatible site.',
    )
    parser.add_argument(
        '--src', default='.',
        help='Source directory containing Obsidian notes (default: repo root)',
    )
    parser.add_argument(
        '--out', default='_site',
        help='Output directory for the built site (default: _site)',
    )
    args = parser.parse_args()

    print(f'Building site: {args.src} → {args.out}')
    build_site(args.src, args.out)


if __name__ == '__main__':
    main()
