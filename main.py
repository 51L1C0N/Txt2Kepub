import os
import json
import shutil
import logging
import uuid
from pathlib import Path
from core.processor import parse_chapters, read_file_content, s2t_convert
from core.engine import generate_epub, run_kepubify
from io_adapters.dropbox_client import DropboxClient

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def load_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def main():
    base_dir = Path(__file__).resolve().parent
    io_config = load_json(base_dir / 'config' / 'io_config.json')
    profile_map = load_json(base_dir / 'config' / 'profile_map.json')
    
    try:
        app_key = os.environ['DROPBOX_APP_KEY']
        app_secret = os.environ['DROPBOX_APP_SECRET']
        refresh_token = os.environ['DROPBOX_REFRESH_TOKEN']
    except KeyError as e:
        logging.error(f"❌ 缺少環境變數: {e}")
        return

    client = DropboxClient(app_key, app_secret, refresh_token)
    
    work_dir = base_dir / 'temp_work'
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir()
    
    kepub_dir = work_dir / "kepub_out"
    kepub_dir.mkdir(exist_ok=True)

    input_base = io_config['directories']['input_base']
    output_base = io_config['directories']['output_base']
    archive_base = io_config['directories']['archive_base']

    for subfolder in io_config['monitor_subfolders']:
        logging.info(f"📂 正在掃描: {subfolder} ...")
        
        target_style_file = profile_map['default_style']
        for mapping in profile_map['mappings']:
            if mapping['keyword'] in subfolder:
                target_style_file = mapping['style_file']
                break
        
        style_path = base_dir / 'styles' / target_style_file
        style_config = load_json(style_path)
        if isinstance(style_config.get('css'), list):
            style_config['css'] = "\n".join(style_config['css'])

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
                client.download_file(file_meta['path_lower'], local_txt_path)
                
                raw_content = read_file_content(local_txt_path)
                if not raw_content:
                    logging.error(f"   ❌ 編碼失敗: {filename}")
                    continue

                processed_content = s2t_convert(raw_content)
                chapters = parse_chapters(processed_content)
                
                # 生成 UUID 檔名的 EPUB
                temp_epub_path = work_dir / f"{safe_id}.epub"
                original_title = Path(filename).stem
                
                generate_epub(original_title, "Unknown", chapters, temp_epub_path, style_config)
                
                # 執行轉換
                if run_kepubify(temp_epub_path, kepub_dir):
                    # 預期輸出
                    expected_output = kepub_dir / f"{safe_id}.kepub.epub"
                    
                    if not expected_output.exists():
                        logging.error(f"   ❌ 轉換後檔案遺失！")
                        logging.error(f"   🔍 現場勘查: kepub_out 目錄下的檔案有: {[f.name for f in kepub_dir.iterdir()]}")
                        continue

                    final_kepub_name = f"{original_title}.kepub.epub"
                    target_output_path = f"{output_base}/{subfolder}/{final_kepub_name}"
                    
                    logging.info(f"   ☁️ 上傳為: {final_kepub_name}")
                    if client.upload_file(expected_output, target_output_path):
                        target_archive_path = f"{archive_base}/{subfolder}/{filename}"
                        client.move_file(file_meta['path_lower'], target_archive_path)
                        logging.info(f"   ✅ 全部完成: {filename}")
                else:
                    logging.error(f"   ❌ Kepubify 轉換指令返回錯誤")
                
            except Exception as e:
                logging.error(f"   ❌ 異常中斷 {filename}: {e}")

    if work_dir.exists():
        shutil.rmtree(work_dir)
    logging.info("🏁 任務結束")

if __name__ == "__main__":
    main()
