VE3 SUITE - HUONG DAN CHAY PORTABLE
===================================

Muc tieu:
- Mot thu muc duy nhat co the copy sang may ao/may khac.
- Van giu 2 tool rieng: srt-to-excel tao SRT/Excel, ve3 tao anh/video.
- Tat ca project dung chung: D:\VE3_SUITE\PROJECTS

Chay:
1. Mo D:\VE3_SUITE\RUN.bat
2. GUI VE3 Suite se mo len voi sidebar giong VE3.
3. Bam "FULL FLOW" de chay day du:
   - Chay noi bo MP3 -> SRT.
   - Chay noi bo SRT -> Excel prompts.
   - Chay noi bo VE3 worker tao anh/video tu Excel.
   - Khong mo GUI con cua SRT-to-Excel hay VE3.
4. Bam "EXCEL TOOL" neu can mo rieng tool tao SRT/Excel de debug.
5. Bam "VE3 TOOL" neu can mo rieng tool tao anh/video de debug.
6. Bang "Project / Excel Tracker" tu theo doi SRT, Excel, chars, scenes, img, vid, music trong PROJECTS.
7. Bam "CHECK ENV" de kiem tra may truoc khi chay.
8. Neu RUN.bat khong hien gi, chay RUN_DEBUG.bat de xem loi Python/package.

Copy sang may khac:
- Copy nguyen thu muc D:\VE3_SUITE.
- Tren may moi, chay INSTALL_REQUIREMENTS.bat mot lan neu thieu thu vien.
- Chay CHECK_ENV.bat de kiem tra Python, pip, ffmpeg, Whisper.
- API keys/config nam trong tung tool con, can kiem tra lai neu may moi khac moi truong.
- De chay nhanh hang ngay, chi can bam RUN.bat.

Duong dan quan trong:
- Project output: PROJECTS\KA5-xxxx
- Excel tool: tools\srt-to-excel
- VE3 tool: tools\ve3
- Suno portable: tools\suno

Luu y:
- Suno da duoc dong goi trong tools\suno de copy sang may ao.
- Khong nen sua truc tiep file trong PROJECTS khi pipeline dang chay.
