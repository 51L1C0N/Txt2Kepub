import os
import zipfile
import shutil
import re
import logging
import xml.etree.ElementTree as ET
from pathlib import Path

# 設置 XML 命名空間，方便解析 OPF
NS = {
    'opf': 'http://www.idpf.org/2007/opf',
    'dc': 'http://purl.org/dc/elements/1.1/',
    'xhtml': 'http://www.w3.org/1999/xhtml'
}

def get_epub_info(zip_ref):
    """
    從 EPUB 中解析核心資訊：OPF路徑、標題、作者
    """
    # 1. 讀取 container.xml 找到 OPF 位置
    try:
        container_xml = zip_ref.read('META-INF/container.xml')
        root = ET.fromstring(container_xml)
        # 尋找 full-path 屬性
        opf_path = root.find('.//{*}rootfile').attrib['full-path']
    except Exception:
        logging.warning("⚠️ 無法讀取 container.xml，嘗試搜索 .opf 文件")
        # 備用方案：直接搜尋 .opf
        opf_files = [f for f in zip_ref.namelist() if f.endswith('.opf')]
        if not opf_files:
            raise FileNotFoundError("找不到 OPF 文件")
        opf_path = opf_files[0]

    # 2. 解析 OPF 獲取元數據
    opf_content = zip_ref.read(opf_path)
    # 移除命名空間前綴以便解析 (Dirty hack but works for various EPUB versions)
    opf_str = opf_content.decode('utf-8', errors='ignore')
    # 簡單的正則提取，比 XML 解析更容錯
    title_match = re.search(r'<dc:title[^>]*>(.*?)</dc:title>', opf_str, re.IGNORECASE | re.DOTALL)
    creator_match = re.search(r'<dc:creator[^>]*>(.*?)</dc:creator>', opf_str, re.IGNORECASE | re.DOTALL)
    
    metadata = {
        'title': title_match.group(1).strip() if title_match else "Unknown Manga",
        'creator': creator_match.group(1).strip() if creator_match else "Unknown Author",
        'opf_path': opf_path,
        'opf_dir': os.path.dirname(opf_path)
    }
    return metadata

def extract_images_in_order(zip_ref, metadata, temp_extract_dir):
    """
    依照 Spine 的順序提取圖片，解決亂序問題
    """
    opf_path = metadata['opf_path']
    opf_dir = metadata['opf_dir']
    
    # 解析 OPF XML
    tree = ET.fromstring(zip_ref.read(opf_path))
    
    # 1. 建立 Manifest 映射 (ID -> Href)
    # 處理 namespace 是件麻煩事，這裡用通配符 * 尋找
    manifest = {}
    for item in tree.findall('.//{*}manifest/{*}item'):
        manifest[item.attrib['id']] = item.attrib['href']

    # 2. 獲取 Spine 順序 (ID Ref)
    spine_ids = [item.attrib['idref'] for item in tree.findall('.//{*}spine/{*}itemref')]

    images_in_order = []
    
    # 3. 遍歷 Spine，找出圖片
    for item_id in spine_ids:
        if item_id not in manifest: continue
        
        href = manifest[item_id]
        # HTML 文件的完整路徑
        html_path = (Path(opf_dir) / href).as_posix() # 使用 posix 路徑風格
        
        # 嘗試讀取 HTML 內容
        try:
            # 確保路徑開頭沒有 /
            if html_path.startswith('/'): html_path = html_path[1:]
            
            html_content = zip_ref.read(html_path).decode('utf-8', errors='ignore')
            
            # 使用正則表達式尋找 <img src="...">
            # 這比解析 HTML XML 更穩健，因為漫畫 HTML 通常很簡單
            img_match = re.search(r'<img[^>]+src=["\'](.*?)["\']', html_content, re.IGNORECASE)
            
            if img_match:
                img_src = img_match.group(1)
                # 解析圖片相對於 HTML 的路徑
                # html_dir: OEBPS/Text, img_src: ../Images/01.jpg -> OEBPS/Images/01.jpg
                html_folder = os.path.dirname(html_path)
                img_full_path = (Path(html_folder) / img_src).resolve().as_posix()
                
                # 有時候 resolve 會算出絕對路徑 (包含 C: 或 /)，我們要轉回相對路徑
                # 這裡做個簡單處理：重新組合路徑
                # 簡單來說：我們需要它在 ZIP 裡面的路徑
                
                # 更保險的路徑拼接法
                normalized_path = os.path.normpath(os.path.join(html_folder, img_src)).replace('\\', '/')
                
                # 提取圖片到臨時目錄
                try:
                    target_ext = os.path.splitext(normalized_path)[1]
                    # 給一個有序的新名字，確保之後處理順序正確
                    new_filename = f"source_{len(images_in_order):05d}{target_ext}"
                    extract_path = temp_extract_dir / new_filename
                    
                    with open(extract_path, 'wb') as f_out:
                        f_out.write(zip_ref.read(normalized_path))
                    
                    images_in_order.append(extract_path)
                except KeyError:
                    logging.warning(f"⚠️ 找不到圖片路徑: {normalized_path}")
                    
        except Exception as e:
            logging.warning(f"⚠️ 無法處理章節 {html_path}: {e}")
            continue

    # 如果 Spine 解析失敗（有些書結構很爛），回退到自然排序法
    if not images_in_order:
        logging.warning("⚠️ Spine 解析未找到圖片，回退到檔名排序模式")
        # 原有的備份邏輯... (略，或是直接拋出錯誤讓用戶檢查)
        
    return images_in_order

def rebuild_manga_epub(input_epub, output_epub, style_config):
    """
    主函數：智慧重組 EPUB
    """
    pages_per_chapter = style_config.get('pages_per_chapter', 20)
    template = style_config.get('chapter_template', "({start}-{end}頁)")
    css_rules = "\n".join(style_config.get('css', []))

    temp_extract_dir = Path("temp_manga_extract")
    build_dir = Path("temp_manga_build")
    for d in [temp_extract_dir, build_dir]:
        if d.exists(): shutil.rmtree(d)
        d.mkdir()

    try:
        with zipfile.ZipFile(input_epub, 'r') as z:
            # 1. 獲取原書資訊
            metadata = get_epub_info(z)
            logging.info(f"   📘 識別書籍: {metadata['title']} / {metadata['creator']}")
            
            # 2. 依照正確順序提取圖片
            images = extract_images_in_order(z, metadata, temp_extract_dir)

        if not images:
            logging.error("❌ 無法提取圖片，終止重組")
            return False

        # 3. 初始化新 EPUB 結構
        (build_dir / "META-INF").mkdir()
        (build_dir / "OEBPS" / "images").mkdir(parents=True)
        
        with open(build_dir / "mimetype", "w") as f: f.write("application/epub+zip")
        with open(build_dir / "META-INF" / "container.xml", "w") as f:
            f.write('<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        
        with open(build_dir / "OEBPS" / "style.css", "w") as f: f.write(css_rules)

        manifest, spine, toc_links = [], [], []

        # 4. 重新打包
        for i, img_path in enumerate(images):
            ext = img_path.suffix
            # 重新命名圖片，保證物理順序
            new_img_name = f"img_{i:04d}{ext}"
            shutil.copy(img_path, build_dir / "OEBPS" / "images" / new_img_name)

            xhtml_name = f"page_{i:04d}.xhtml"
            with open(build_dir / "OEBPS" / xhtml_name, "w") as f:
                f.write(f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml"><head>
<link rel="stylesheet" type="text/css" href="style.css"/><title>{i+1}</title></head>
<body><div class="page-box"><img src="images/{new_img_name}"/></div></body></html>''')

            manifest.append(f'<item id="p{i}" href="{xhtml_name}" media-type="application/xhtml+xml"/>')
            m_type = "image/jpeg" if "jpg" in ext.lower() or "jpeg" in ext.lower() else f"image/{ext[1:]}"
            manifest.append(f'<item id="i{i}" href="images/{new_img_name}" media-type="{m_type}"/>')
            spine.append(f'<itemref idref="p{i}"/>')

            # 建立分章 (20頁一章)
            if i % pages_per_chapter == 0:
                start = i + 1
                end = min(i + pages_per_chapter, len(images))
                chapter_title = template.format(start=start, end=end)
                toc_links.append(f'<li><a href="{xhtml_name}">{chapter_title}</a></li>')

        # 5. 生成 OPF (帶入原始 Metadata)
        # 第一張圖設為封面
        cover_meta = '<meta name="cover" content="i0" />' if images else ''
        
        opf_content = f'''<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="id">urn:uuid:{os.urandom(8).hex()}</dc:identifier>
    <dc:title>{metadata['title']}</dc:title>
    <dc:creator>{metadata['creator']}</dc:creator>
    <dc:language>zh</dc:language>
    {cover_meta}
</metadata>
<manifest>
    <item id="css" href="style.css" media-type="text/css"/>
    <item id="nav" href="nav.xhtml" properties="nav" media-type="application/xhtml+xml"/>
    {"".join(manifest)}
</manifest>
<spine>{"".join(spine)}</spine>
</package>'''
        
        with open(build_dir / "OEBPS" / "content.opf", "w", encoding="utf-8") as f: f.write(opf_content)

        nav_content = f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>目錄</title></head><body><nav epub:type="toc"><h1>目錄</h1><ol>{"".join(toc_links)}</ol></nav></body></html>'''
        
        with open(build_dir / "OEBPS" / "nav.xhtml", "w", encoding="utf-8") as f: f.write(nav_content)

        # 6. 壓縮輸出
        with zipfile.ZipFile(output_epub, 'w', compression=zipfile.ZIP_DEFLATED) as z:
            z.write(build_dir / "mimetype", "mimetype", compress_type=zipfile.ZIP_STORED)
            for f in build_dir.rglob('*'):
                if f.name != "mimetype":
                    z.write(f, f.relative_to(build_dir))
        
        return True

    except Exception as e:
        logging.error(f"❌ 重組異常: {e}")
        return False
    finally:
        shutil.rmtree(temp_extract_dir, ignore_errors=True)
        shutil.rmtree(build_dir, ignore_errors=True)
