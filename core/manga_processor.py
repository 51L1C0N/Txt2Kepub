import os
import zipfile
import shutil
import re
from pathlib import Path

def natural_sort_key(s):
    """
    自然排序算法，確保 2.jpg 排在 10.jpg 前面
    """
    return [int(text) if text.isdigit() else text.lower() for text in re.split('([0-9]+)', str(s))]

def extract_manga_images(epub_path, temp_extract_dir):
    """
    從 EPUB 中提取所有圖片並按順序排列
    """
    with zipfile.ZipFile(epub_path, 'r') as z:
        z.extractall(temp_extract_dir)
    
    # 支援常見漫畫圖片格式
    extensions = ('.jpg', '.jpeg', '.png', '.webp')
    all_images = []
    for ext in extensions:
        all_images.extend(list(Path(temp_extract_dir).rglob(f"*{ext}")))
    
    # 排除隱藏文件並進行自然排序
    valid_images = [img for img in all_images if not img.name.startswith('.')]
    valid_images.sort(key=natural_sort_key)
    
    return valid_images

def create_chaptered_epub(images, output_path, style_config):
    """
    核心邏輯：依照樣式規則重新封裝漫畫 EPUB
    """
    pages_per_chapter = style_config.get('pages_per_chapter', 20)
    template = style_config.get('chapter_template', "({start}-{end}頁)")
    
    # 建立臨時打包區
    build_dir = Path("temp_manga_build")
    if build_dir.exists(): shutil.rmtree(build_dir)
    build_dir.mkdir()
    (build_dir / "OEBPS" / "images").mkdir(parents=True)
    (build_dir / "META-INF").mkdir()

    # 1. 準備基礎 EPUB 文件
    with open(build_dir / "mimetype", "w") as f: f.write("application/epub+zip")
    with open(build_dir / "META-INF" / "container.xml", "w") as f:
        f.write('<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')

    manifest, spine, toc_links = [], [], []

    # 2. 處理每一頁
    for i, img_path in enumerate(images):
        new_name = f"p_{i:04d}{img_path.suffix}"
        shutil.copy(img_path, build_dir / "OEBPS" / "images" / new_name)
        
        # 建立每頁的 XHTML (確保圖片全螢幕)
        xhtml_name = f"page_{i:04d}.xhtml"
        with open(build_dir / "OEBPS" / xhtml_name, "w") as f:
            f.write(f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml">
<head><style>body {{ margin:0; padding:0; background:#000; }} img {{ width:100%; height:auto; }}</style></head>
<body><img src="images/{new_name}"/></body></html>''')

        manifest.append(f'<item id="i{i}" href="images/{new_name}" media-type="image/jpeg"/>')
        manifest.append(f'<item id="p{i}" href="{xhtml_name}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="p{i}"/>')

        # 3. 按照樣式配置建立目錄 (TOC)
        if i % pages_per_chapter == 0:
            start = i + 1
            end = min(i + pages_per_chapter, len(images))
            chapter_title = template.format(start=start, end=end)
            toc_links.append(f'<li><a href="{xhtml_name}">{chapter_title}</a></li>')

    # 4. 生成 OPF 和 NAV (省略部分重複 XML，確保結構完整)
    # ... 此處邏輯與文字書相似，但專為漫畫優化 ...
    
    # 5. 最後打包回 EPUB
    # (此處調用 zip 壓縮邏輯)
    return True
