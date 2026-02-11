import os
import json
import shutil
import logging
from pathlib import Path
from core.manga_processor import rebuild_manga_epub  # 我們稍後會完善這個處理器
from core.engine import run_kepubify
from io_adapters.dropbox_client import DropboxClient

# 設定日誌
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def load_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def main():
    base_dir = Path(__file__).resolve().parent
    
    # 1. 載入配置與樣式
    manga_config = load_json(base_dir / 'config' / 'manga_config.json')
    manga_style = load_json(base_dir / 'styles' / 'manga_standard.json')
    
    # 2. 初始化 Dropbox (使用現有的 Secrets)
    try:
        client = DropboxClient(
            os.environ['DROPBOX_APP_KEY'],
            os.environ['DROPBOX_APP_SECRET'],
            os.environ['DROPBOX_REFRESH_TOKEN']
        )
    except KeyError as e:
        logging.error(f"❌ 缺少 Dropbox 認證環境變數: {e}")
        return

    # 3. 準備臨時工作空間
    work_dir = base_dir / 'temp_manga_work'
    if work_dir.exists(): shutil.rmtree(work_dir)
    work_dir.mkdir()
    
    kepub_out_dir = work_dir / "kepub_out"
    kepub_out_dir.mkdir()

    # 4. 開始處理不同資料夾
    input_base = manga_config['directories']['input_base']
    output_base = manga_config['directories']['output_base']
    archive_base = manga_config['directories']['archive_base']

    for sub in manga_config['monitor_subfolders']:
        logging.info(f"🎞️ 掃描漫畫目錄: {sub}")
        current_input_path = f"{input_base}/{sub}"
        files = client.list_files(current_input_path)

        for f_meta in files:
            fname = f_meta['name']
            if not fname.lower().endswith('.epub'): continue

            logging.info(f"   🚀 處理漫畫: {fname}")
            local_src = work_dir / fname
            client.download_file(f_meta['path_lower'], local_src)

            # --- 核心邏輯切換 ---
            ready_to_convert_epub = local_src
            
            if sub == "001":
                logging.info("   🧩 [模式 001] 執行 EPUB 拆解與重組...")
                rebuild_path = work_dir / f"rebuilt_{fname}"
                # 這裡調用 processor 進行重組
                if rebuild_manga_epub(local_src, rebuild_path, manga_style):
                    ready_to_convert_epub = rebuild_path
                else:
                    logging.error(f"   ❌ 重組失敗: {fname}")
                    continue
            else:
                logging.info("   ⏩ [模式 002] 跳過重組，直接轉檔")

            # --- 轉檔與上傳 ---
            if run_kepubify(ready_to_convert_epub, kepub_out_dir):
                # 尋找輸出檔案 (考慮到可能存在的 _converted 後綴)
                kepub_file = next(kepub_out_dir.glob("*.kepub.epub"), None)
                
                if kepub_file:
                    final_name = fname.replace('.epub', '.kepub.epub')
                    target_path = f"{output_base}/{sub}/{final_name}"
                    
                    logging.info(f"   ☁️ 上傳 KePub: {final_name}")
                    if client.upload_file(kepub_file, target_path):
                        # 歸檔原始檔
                        archive_path = f"{archive_base}/{sub}/{fname}"
                        client.move_file(f_meta['path_lower'], archive_path)
                        logging.info(f"   ✅ 完成: {fname}")
                
                # 清理這本書的轉檔快取
                for f in kepub_out_dir.iterdir(): os.remove(f)
            else:
                logging.error(f"   ❌ Kepubify 執行失敗: {fname}")

    # 清理總臨時區
    shutil.rmtree(work_dir)
    logging.info("🏁 漫畫轉檔任務結束")

if __name__ == "__main__":
    main()
