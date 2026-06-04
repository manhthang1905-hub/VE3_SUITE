"""
Startup Manager — GUI để quản lý ứng dụng khởi động cùng Windows.
"""
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path

STARTUP_DIR = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def get_startup_items():
    items = []
    if STARTUP_DIR.exists():
        for f in STARTUP_DIR.iterdir():
            if f.suffix.lower() == ".lnk":
                try:
                    import win32com.client
                    shell = win32com.client.Dispatch("WScript.Shell")
                    shortcut = shell.CreateShortCut(str(f))
                    items.append({"name": f.stem, "target": shortcut.TargetPath, "file": f})
                except Exception:
                    items.append({"name": f.stem, "target": "(unknown)", "file": f})
    return items


def add_startup(name, target_path):
    try:
        import win32com.client
        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortCut(str(STARTUP_DIR / f"{name}.lnk"))
        shortcut.TargetPath = target_path
        shortcut.WorkingDirectory = str(Path(target_path).parent)
        shortcut.Save()
        return True
    except ImportError:
        import subprocess
        cmd = (
            f'$s=(New-Object -COM WScript.Shell).CreateShortcut("{STARTUP_DIR / f"{name}.lnk"}");'
            f'$s.TargetPath="{target_path}";'
            f'$s.WorkingDirectory="{Path(target_path).parent}";'
            f'$s.Save()'
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                       capture_output=True, timeout=10)
        return True
    except Exception:
        return False


def remove_startup(filepath):
    try:
        Path(filepath).unlink()
        return True
    except Exception:
        return False


class StartupManagerGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Startup Manager")
        self.geometry("600x420")
        self.resizable(False, False)
        self.configure(bg="#1e1e2e")

        BG = "#1e1e2e"
        FG = "#cdd6f4"
        BG2 = "#313244"
        GREEN = "#a6e3a1"
        RED = "#f38ba8"
        BLUE = "#89b4fa"
        BD = "#45475a"

        # Header
        tk.Label(self, text="Startup Manager", font=("Segoe UI", 16, "bold"),
                 bg=BG, fg=FG).pack(pady=(15, 5))
        tk.Label(self, text="Quản lý ứng dụng khởi động cùng Windows",
                 font=("Segoe UI", 9), bg=BG, fg=BD).pack()

        # List frame
        list_frame = tk.Frame(self, bg=BG2, bd=1, relief="solid", highlightbackground=BD)
        list_frame.pack(fill="both", expand=True, padx=20, pady=10)

        # Header row
        header = tk.Frame(list_frame, bg="#45475a")
        header.pack(fill="x")
        tk.Label(header, text="Tên", width=20, anchor="w", font=("Segoe UI", 9, "bold"),
                 bg="#45475a", fg=FG).pack(side="left", padx=(10, 0), pady=4)
        tk.Label(header, text="Đường dẫn", anchor="w", font=("Segoe UI", 9, "bold"),
                 bg="#45475a", fg=FG).pack(side="left", fill="x", expand=True, padx=5, pady=4)

        # Scrollable list
        self.list_canvas = tk.Canvas(list_frame, bg=BG2, highlightthickness=0)
        scrollbar = tk.Scrollbar(list_frame, orient="vertical", command=self.list_canvas.yview)
        self.list_inner = tk.Frame(self.list_canvas, bg=BG2)
        self.list_inner.bind("<Configure>", lambda e: self.list_canvas.configure(scrollregion=self.list_canvas.bbox("all")))
        self.list_canvas.create_window((0, 0), window=self.list_inner, anchor="nw")
        self.list_canvas.configure(yscrollcommand=scrollbar.set)
        self.list_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Buttons
        btn_frame = tk.Frame(self, bg=BG)
        btn_frame.pack(fill="x", padx=20, pady=(0, 15))

        tk.Button(btn_frame, text="+ Thêm ứng dụng", command=self._add,
                  bg=GREEN, fg="#1e1e2e", font=("Segoe UI", 10, "bold"),
                  relief="flat", cursor="hand2", padx=15, pady=5).pack(side="left")

        tk.Button(btn_frame, text="Mở thư mục Startup", command=self._open_folder,
                  bg=BLUE, fg="#1e1e2e", font=("Segoe UI", 10),
                  relief="flat", cursor="hand2", padx=15, pady=5).pack(side="right")

        self._refresh()

    def _refresh(self):
        for w in self.list_inner.winfo_children():
            w.destroy()

        items = get_startup_items()
        if not items:
            tk.Label(self.list_inner, text="Chưa có ứng dụng nào khởi động cùng Windows",
                     font=("Segoe UI", 10), bg="#313244", fg="#6c7086",
                     pady=20).pack(fill="x")
            return

        for i, item in enumerate(items):
            bg = "#313244" if i % 2 == 0 else "#2a2a3c"
            row = tk.Frame(self.list_inner, bg=bg)
            row.pack(fill="x")

            tk.Label(row, text=item["name"], width=20, anchor="w",
                     font=("Segoe UI", 10, "bold"), bg=bg, fg="#cdd6f4").pack(side="left", padx=(10, 0), pady=6)

            target = item["target"]
            if len(target) > 45:
                target = "..." + target[-42:]
            tk.Label(row, text=target, anchor="w",
                     font=("Consolas", 8), bg=bg, fg="#a6adc8").pack(side="left", fill="x", expand=True, padx=5, pady=6)

            tk.Button(row, text="✕", width=3, command=lambda f=item["file"], n=item["name"]: self._remove(f, n),
                      bg="#f38ba8", fg="#1e1e2e", font=("Segoe UI", 9, "bold"),
                      relief="flat", cursor="hand2").pack(side="right", padx=8, pady=4)

    def _add(self):
        filepath = filedialog.askopenfilename(
            title="Chọn file để khởi động cùng Windows",
            filetypes=[("All files", "*.*"), ("Batch", "*.bat"), ("Executable", "*.exe"), ("Python", "*.py")]
        )
        if not filepath:
            return

        name = Path(filepath).stem
        name = tk.simpledialog.askstring("Đặt tên", "Tên hiển thị:", initialvalue=name, parent=self)
        if not name:
            return

        if add_startup(name, filepath):
            self._refresh()
        else:
            messagebox.showerror("Lỗi", "Không thể thêm vào Startup")

    def _remove(self, filepath, name):
        if messagebox.askyesno("Xác nhận", f"Xóa '{name}' khỏi khởi động?"):
            remove_startup(filepath)
            self._refresh()

    def _open_folder(self):
        os.startfile(str(STARTUP_DIR))


if __name__ == "__main__":
    try:
        import tkinter.simpledialog
    except ImportError:
        pass
    app = StartupManagerGUI()
    app.mainloop()
