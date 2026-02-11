import os
import json
import shutil
import logging
from pathlib import Path
from core.manga_processor import rebuild_manga_epub
from core.engine import run_kepubify
# 關鍵差異：引用 Google Drive Client
from io_adapters.google_drive_client import GoogleDriveClient

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def load_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def main():
    base_dir = Path(__file__).resolve().parent
    
    # 共用漫畫設定與樣式
    manga_config = load_json(base_dir / 'config' / 'manga_config.json')
    manga_style = load_json(base_dir / 'styles' / 'manga_standard.json')
    
    try:
        service_account_json = os.environ['GOOGLE_SERVICE_ACCOUNT_JSON']
        client = GoogleDriveClient(service_account_json, root_folder_name="Ebook-Converter")
    except KeyError:
        logging.error("❌ 缺少環境變數: GOOGLE_SERVICE_ACCOUNT_JSON")
        return
    except Exception as e:
        logging.error(f"❌ Google Drive 連線失敗: {e}")
        return

    work_dir = base_dir / 'temp_manga_drive_work'
    if work_dir.exists(): shutil.rmtree(work_dir)
    work_dir.mkdir()
    
    kepub_out_dir = work_dir / "kepub_out"
    kepub_out_dir.mkdir()

    input_base = manga_config['directories']['input_base']
    output_base = manga_config['directories']['output_base']
    archive_base = manga_config['directories']['archive_base']

    for sub in manga_config['monitor_subfolders']:
        logging.info(f"🎞️ [Drive] 掃描漫畫目錄: {sub}")
        current_input_path = f"{input_base}/{sub}"
        files = client.list_files(current_input_path)

        for f_meta in files:
            fname = f_meta['name']
            if not fname.lower().endswith('.epub'): continue

            logging.info(f"   🚀 處理漫畫: {fname}")
            local_src = work_dir / fname
            client.download_file(f_meta['path_lower'], local_src)

            # 核心邏輯 (001重組 / 002直通)
            ready_to_convert_epub = local_src
            
            if sub == "001":
                logging.info("   🧩 [模式 001] 執行 EPUB 重組...")
                rebuild_path = work_dir / f"rebuilt_{fname}"
                if rebuild_manga_epub(local_src, rebuild_path, manga_style):
                    ready_to_convert_epub = rebuild_path
                else:
                    logging.error(f"   ❌ 重組失敗: {fname}")
                    continue
            else:
                logging.info("   ⏩ [模式 002] 直通轉檔")

            # 轉檔與上傳
            if run_kepubify(ready_to_convert_epub, kepub_out_dir):
                kepub_file = next(kepub_out_dir.glob("*.kepub.epub"), None)
                
                if kepub_file:
                    final_name = fname.replace('.epub', '.kepub.epub')
                    target_path = f"{output_base}/{sub}/{final_name}"
                    
                    logging.info(f"   ☁️ 上傳 KePub: {final_name}")
                    if client.upload_file(kepub_file, target_path):
                        archive_path = f"{archive_base}/{sub}/{fname}"
                        client.move_file(f_meta['path_lower'], archive_path)
                        logging.info(f"   ✅ 完成: {fname}")
                
                # 清理
                for f in kepub_out_dir.iterdir(): os.remove(f)

    shutil.rmtree(work_dir)
    logging.info("🏁 [Drive] 漫畫任務結束")

if __name__ == "__main__":
    main()
