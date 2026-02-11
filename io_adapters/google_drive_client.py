import os
import json
import io
import logging
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

class GoogleDriveClient:
    def __init__(self, service_account_json_content, root_folder_name="Ebook-Converter"):
        """
        初始化 Google Drive 客戶端
        :param service_account_json_content: GitHub Secret 中的 JSON 字串
        :param root_folder_name: 您在 Drive 建立的根目錄名稱
        """
        scope = ['https://www.googleapis.com/auth/drive']
        
        # 從 JSON 字串載入憑證
        info = json.loads(service_account_json_content)
        creds = service_account.Credentials.from_service_account_info(info, scopes=scope)
        self.service = build('drive', 'v3', credentials=creds)
        
        # 尋找根目錄 ID
        self.root_id = self._find_id_by_name(root_folder_name)
        if not self.root_id:
            raise FileNotFoundError(f"❌ 找不到根目錄: {root_folder_name} (請確認已共用給機器人)")
        logging.info(f"✅ Google Drive 連線成功，根目錄 ID: {self.root_id}")

    def _find_id_by_name(self, name, parent_id=None):
        """在指定父資料夾下尋找檔案/資料夾 ID"""
        query = f"name = '{name}' and trashed = false"
        if parent_id:
            query += f" and '{parent_id}' in parents"
        
        results = self.service.files().list(q=query, fields="files(id, name)").execute()
        files = results.get('files', [])
        return files[0]['id'] if files else None

    def _ensure_folder_path(self, path):
        """
        解析路徑並回傳最終資料夾的 ID (如果不存在則自動建立)
        path: 例如 /novel/txt/001
        """
        # 移除開頭的 / 並分割
        parts = [p for p in path.strip("/").split("/") if p]
        
        current_parent_id = self.root_id
        
        for part in parts:
            # 嘗試在當前層級尋找
            found_id = self._find_id_by_name(part, current_parent_id)
            
            if found_id:
                current_parent_id = found_id
            else:
                # 找不到就建立
                file_metadata = {
                    'name': part,
                    'mimeType': 'application/vnd.google-apps.folder',
                    'parents': [current_parent_id]
                }
                folder = self.service.files().create(body=file_metadata, fields='id').execute()
                current_parent_id = folder.get('id')
                logging.info(f"   📁 自動建立資料夾: {part}")
        
        return current_parent_id

    def list_files(self, folder_path):
        """列出指定路徑下的檔案 (模擬 Dropbox 的 list_files)"""
        try:
            folder_id = self._ensure_folder_path(folder_path)
            # 只列出檔案，不列出資料夾
            query = f"'{folder_id}' in parents and mimeType != 'application/vnd.google-apps.folder' and trashed = false"
            results = self.service.files().list(q=query, fields="files(id, name)").execute()
            
            # 轉換成類似 Dropbox 的格式，方便 main.py 使用
            file_list = []
            for f in results.get('files', []):
                file_list.append({
                    'name': f['name'],
                    'id': f['id'],
                    'path_display': f"{folder_path}/{f['name']}",
                    'path_lower': f['id']  # 在 Drive 模式下，我們用 ID 來下載
                })
            return file_list
        except Exception as e:
            logging.error(f"❌ 無法讀取目錄 {folder_path}: {e}")
            return []

    def download_file(self, file_id, local_path):
        """下載檔案 (注意：這裡的第一個參數是 file_id)"""
        request = self.service.files().get_media(fileId=file_id)
        fh = io.FileIO(local_path, 'wb')
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while done is False:
            status, done = downloader.next_chunk()

    def upload_file(self, local_path, remote_path):
        """上傳檔案"""
        try:
            # 解析遠端路徑，分出目錄和檔名
            folder_path = os.path.dirname(remote_path)
            file_name = os.path.basename(remote_path)
            
            # 獲取目標資料夾 ID
            folder_id = self._ensure_folder_path(folder_path)
            
            # 檢查檔案是否已存在 (避免重複上傳)
            existing_id = self._find_id_by_name(file_name, folder_id)
            if existing_id:
                # 這裡可以選擇覆蓋或跳過，目前選擇刪除舊的再上傳
                self.service.files().delete(fileId=existing_id).execute()

            file_metadata = {
                'name': file_name,
                'parents': [folder_id]
            }
            media = MediaFileUpload(local_path, resumable=True)
            
            self.service.files().create(body=file_metadata, media_body=media, fields='id').execute()
            return True
        except Exception as e:
            logging.error(f"❌ 上傳失敗 {remote_path}: {e}")
            return False

    def move_file(self, file_id, dest_path):
        """移動檔案 (Drive 的移動其實是修改 parents 屬性)"""
        try:
            # 獲取檔案目前的 parent
            file = self.service.files().get(fileId=file_id, fields='parents').execute()
            previous_parents = ",".join(file.get('parents'))
            
            # 獲取目標資料夾 ID
            dest_folder_path = os.path.dirname(dest_path) # 例如 /novel/txt/已處理/001
            new_parent_id = self._ensure_folder_path(dest_folder_path)
            
            # 執行移動
            self.service.files().update(
                fileId=file_id,
                addParents=new_parent_id,
                removeParents=previous_parents,
                fields='id, parents'
            ).execute()
            return True
        except Exception as e:
            logging.error(f"❌ 移動失敗: {e}")
            return False
