import os
import json
import shutil
import logging
import uuid
from pathlib import Path
from core.processor import parse_chapters, read_file_content, s2t_convert
from core.engine import generate_epub, run_kepubify
# 關鍵差異：引用 Google Drive Client
from io_adapters.google_drive_client import GoogleDriveClient

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def load_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def main():
    base_dir = Path(__file__).resolve().parent
    # 共用原本的設定檔
    io_config = load_json(base_dir / 'config' / 'drive_io_config.json')
    profile_map = load_json(base_dir / 'config' / 'profile_map.json')
    
    # 初始化 Google Drive 連線
    try:
        client_id = os.environ['GOOGLE_CLIENT_ID']
        client_secret = os.environ['GOOGLE_CLIENT_SECRET']
        refresh_token = os.environ['GOOGLE_REFRESH_TOKEN']
        
        # 這裡填入您在 Google Drive 建立的根目錄名稱
        client = GoogleDriveClient(client_id, client_secret, refresh_token, root_folder_name="Ebook-Converter")
    except KeyError as e:
        logging.error(f"❌ 缺少環境變數: {e}")
        return
    except Exception as e:
        logging.error(f"❌ Google Drive 連線失敗: {e}")
        return

    # 建立臨時工作區
    work_dir = base_dir / 'temp_drive_work'
    if work_dir.exists(): shutil.rmtree(work_dir)
    work_dir.mkdir()
    
    kepub_dir = work_dir / "kepub_out"
    kepub_dir.mkdir(exist_ok=True)

    # 讀取路徑配置
    input_base = io_config['directories']['input_base']
    archive_base = io_config['directories']['archive_base']
    epub_base = io_config['directories']['epub_base']
    output_base = io_config['directories']['output_base']

    for subfolder in io_config['monitor_subfolders']:
        logging.info(f"📂 [Drive] 掃描小說資料夾: {subfolder} ...")
        
        # 樣式選擇邏輯 (共用)
        target_style_file = profile_map['default_style']
        for mapping in profile_map['mappings']:
            if mapping['keyword'] in subfolder:
                target_style_file = mapping['style_file']
                break
        
        style_path = base_dir / 'styles' / target_style_file
        style_config = load_json(style_path)
        if isinstance(style_config.get('css'), list):
            style_config['css'] = "\n".join(style_config['css'])

        # Google Drive 列表
        current_input_path = f"{input_base}/{subfolder}"
        files = client.list_files(current_input_path)
        
        if not files:
            continue

        for file_meta in files:
            filename = file_meta['name']
            if not filename.lower().endswith('.txt'):
                continue
                
            logging.info(f"   ⬇️ 處理新書: {filename}")
            safe_id = uuid.uuid4().hex
            local_txt_path = work_dir / f"{safe_id}.txt"
            
            try:
                # 1. 下載 (Drive Client 會使用 ID 下載)
                client.download_file(file_meta['path_lower'], local_txt_path)
                
                # 2. 文本處理 (共用核心邏輯)
                raw_content = read_file_content(local_txt_path)
                if not raw_content:
                    logging.error(f"   ❌ 編碼失敗: {filename}")
                    continue
                processed_content = s2t_convert(raw_content)
                chapters = parse_chapters(processed_content)
                
                # 3. 生成 EPUB
                temp_epub_path = work_dir / f"{safe_id}.epub"
                original_title = Path(filename).stem
                generate_epub(original_title, "Unknown", chapters, temp_epub_path, style_config)
                
                # 上傳標準 EPUB
                final_epub_name = f"{original_title}.epub"
                target_epub_path = f"{epub_base}/{subfolder}/{final_epub_name}"
                logging.info(f"   ☁️ 備份 EPUB: {final_epub_name}")
                client.upload_file(temp_epub_path, target_epub_path)
                
                # 4. 轉換 KePub
                if run_kepubify(temp_epub_path, kepub_dir):
                    possible_names = [f"{safe_id}.kepub.epub", f"{safe_id}_converted.kepub.epub"]
                    found_file = next((kepub_dir / n for n in possible_names if (kepub_dir / n).exists()), None)
                    
                    if found_file:
                        final_kepub_name = f"{original_title}.kepub.epub"
                        target_output_path = f"{output_base}/{final_kepub_name}"
                        logging.info(f"   ☁️ 上傳 KePub: {final_kepub_name}")
                        
                        if client.upload_file(found_file, target_output_path):
                            # 5. 歸檔 (移動原始 TXT)
                            target_archive_path = f"{archive_base}/{subfolder}/{filename}"
                            client.move_file(file_meta['path_lower'], target_archive_path)
                            logging.info(f"   ✅ 完成: {filename}")
                
            except Exception as e:
                logging.error(f"   ❌ 處理異常 {filename}: {e}")

    if work_dir.exists():
        shutil.rmtree(work_dir)
    logging.info("🏁 [Drive] 小說任務結束")

if __name__ == "__main__":
    main()
