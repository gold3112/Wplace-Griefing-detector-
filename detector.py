import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk, ImageChops
import requests
import io
import time
import os
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

plt.rcParams['font.family'] = 'Meiryo'
plt.rcParams['axes.unicode_minus'] = False

# --- 設定 ---
TILE_URL = "https://backend.wplace.live/files/s0/tiles/1819/806.png"
SEAL_IMAGE_PATH = "kiku.png"
CHECK_INTERVAL_MS = 1000

# レベル別しきい値初期値（％）
VANDALISM_LEVELS = [
    [25.0, "軽度"],
    [50.0, "中度"],
    [75.0, "重度"]
]

def get_image_from_url(url):
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return Image.open(io.BytesIO(response.content)).convert("RGB")
    except requests.exceptions.RequestException as e:
        print(f"画像取得エラー: {e}")
        return None

def compare_images(img1, img2):
    if img1.size != img2.size:
        min_width = min(img1.width, img2.width)
        min_height = min(img1.height, img2.height)
        img1 = img1.crop((0,0,min_width,min_height))
        img2 = img2.crop((0,0,min_width,min_height))
    diff = ImageChops.difference(img1, img2)
    if diff.getbbox() is None:
        return 0.0, diff
    diff_non_zero = sum(1 for pixel in diff.getdata() if pixel != (0,0,0))
    total_pixels = img1.width * img1.height
    return (diff_non_zero / total_pixels)*100, diff

class VandalismDetectorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("wplace 荒らし検出")
        self.root.attributes('-fullscreen', True)
        self.root.configure(bg='black')

        style = ttk.Style()
        style.theme_use('clam')
        style.configure('TLabel', background='#1a1a1a', foreground='#e0e0e0', font=("Helvetica", 14))
        style.configure('TFrame', background='#1a1a1a')
        style.configure('TButton', background='#333333', foreground='#e0e0e0', font=("Helvetica", 12))
        style.map('TButton', background=[('active', '#555555')])
        style.configure('TEntry', fieldbackground='#333333', foreground='#e0e0e0', insertbackground='#e0e0e0')
        style.configure('Status.TLabel', background='#1a1a1a', foreground='#00ff00', font=("Helvetica", 20, "bold"), padding=15)
        style.configure('ImageTitle.TLabel', background='#1a1a1a', foreground='#e0e0e0', font=("Helvetica", 16, "bold"))
        style.configure('Info.TLabel', background='#1a1a1a', foreground='#e0e0e0', font=("Helvetica", 12))

        self.seal_image = self.load_and_prepare_seal_image()
        if not self.seal_image:
            root.destroy()
            return

        self.DISPLAY_IMAGE_SIZE = (450, 450)
        # しきい値Entry用
        self.threshold_vars = [tk.StringVar(value=str(v[0])) for v in VANDALISM_LEVELS]
        self.check_interval_var = tk.StringVar(value=str(CHECK_INTERVAL_MS // 1000))

        self.last_detection_time_var = tk.StringVar(value="なし")
        self.total_detections_var = tk.IntVar(value=0)
        self.current_tile_url_var = tk.StringVar(value=TILE_URL)
        self.seal_image_path_var = tk.StringVar(value="読み込み中...")
        self.uptime_var = tk.StringVar(value="00:00:00")
        self.start_time = time.time()

        self.diff_history = []
        self.time_history = []
        self.fig, self.ax = plt.subplots(figsize=(5,3), dpi=100)
        self.setup_graph()

        self.status_var = tk.StringVar(value="初期化中...")

        self.setup_gui()
        self.perform_check()
        self.update_uptime_display()

    def setup_graph(self):
        self.ax.set_title("差分パーセンテージの推移", color='white')
        self.ax.set_xlabel("時間 (秒)", color='white')
        self.ax.set_ylabel("差分 (%)", color='white')
        self.ax.set_ylim(0, 100)
        self.ax.set_facecolor('black')
        self.fig.patch.set_facecolor('black')
        for spine in self.ax.spines.values():
            spine.set_color('white')
        self.ax.tick_params(axis='x', colors='white')
        self.ax.tick_params(axis='y', colors='white')

    def setup_gui(self):
        self.root.grid_rowconfigure(0, weight=0)
        self.root.grid_rowconfigure(1, weight=1)
        self.root.grid_rowconfigure(2, weight=0)
        self.root.grid_rowconfigure(3, weight=0)
        self.root.grid_columnconfigure(0, weight=1)

        self.status_label = ttk.Label(self.root, textvariable=self.status_var, style='Status.TLabel')
        self.status_label.grid(row=0, column=0, pady=10)

        self.image_frame = ttk.Frame(self.root, padding=10)
        self.image_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)
        for i in range(3):
            self.image_frame.grid_columnconfigure(i, weight=1)
        self.image_frame.grid_rowconfigure(0, weight=1)

        self.realtime_frame = ttk.Frame(self.image_frame)
        self.realtime_frame.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        ttk.Label(self.realtime_frame, text="リアルタイム", style='ImageTitle.TLabel').pack()
        self.realtime_image_label = ttk.Label(self.realtime_frame)
        self.realtime_image_label.pack()

        self.diff_frame = ttk.Frame(self.image_frame)
        self.diff_frame.grid(row=0, column=1, sticky="nsew", padx=10, pady=10)
        ttk.Label(self.diff_frame, text="差分", style='ImageTitle.TLabel').pack()
        self.diff_image_label = ttk.Label(self.diff_frame)
        self.diff_image_label.pack()

        placeholder = ImageTk.PhotoImage(Image.new('RGB', self.DISPLAY_IMAGE_SIZE, 'gray'))
        self.realtime_image_label.config(image=placeholder)
        self.diff_image_label.config(image=placeholder)
        self.realtime_image_label.image = placeholder
        self.diff_image_label.image = placeholder

        self.graph_frame = ttk.Frame(self.image_frame)
        self.graph_frame.grid(row=0, column=2, sticky="nsew", padx=10, pady=10)
        ttk.Label(self.graph_frame, text="差分グラフ", style='ImageTitle.TLabel').pack()
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.graph_frame)
        self.canvas_widget = self.canvas.get_tk_widget()
        self.canvas_widget.pack(side=tk.TOP, fill=tk.BOTH, expand=1)
        self.canvas.draw()

        # しきい値Entry
        self.param_frame = ttk.Frame(self.root, padding=10)
        self.param_frame.grid(row=2, column=0, pady=10)
        for i in range(4):
            self.param_frame.grid_columnconfigure(i, weight=1)

        for i, (var, v) in enumerate(zip(self.threshold_vars, VANDALISM_LEVELS)):
            ttk.Label(self.param_frame, text=f"{v[1]}しきい値 (%):", style='Info.TLabel').grid(row=0, column=i*2, sticky="w")
            entry = ttk.Entry(self.param_frame, textvariable=var, width=10)
            entry.grid(row=0, column=i*2+1, sticky="w")
            vcmd = (self.root.register(self.validate_threshold_input), '%P')
            entry.config(validate="key", validatecommand=vcmd)

        # チェック間隔
        ttk.Label(self.param_frame, text="チェック間隔 (秒):").grid(row=1, column=0, sticky="w")
        self.interval_entry = ttk.Entry(self.param_frame, textvariable=self.check_interval_var, width=10)
        self.interval_entry.grid(row=1, column=1, sticky="w")
        vcmd_interval = (self.root.register(self.validate_interval_input), '%P')
        self.interval_entry.config(validate="key", validatecommand=vcmd_interval)

        self.info_frame = ttk.Frame(self.root, padding=10)
        self.info_frame.grid(row=3, column=0, pady=10)
        self.info_frame.grid_columnconfigure(0, weight=1)
        self.info_frame.grid_columnconfigure(1, weight=1)
        ttk.Label(self.info_frame, text="稼働時間:", style='Info.TLabel').grid(row=0, column=0, sticky="w")
        ttk.Label(self.info_frame, textvariable=self.uptime_var, style='Info.TLabel').grid(row=0, column=1, sticky="w")
        ttk.Label(self.root, text="Wplace皇居荒らし監視tool　　制作者:AI(Gemini)とGOLD+ゴリ", style='Info.TLabel').grid(row=4, column=0, pady=5)

    def validate_threshold_input(self, p):
        if p == "":
            return True
        try:
            value = float(p)
            return 0.0 <= value <= 100.0
        except ValueError:
            return False

    def validate_interval_input(self, p):
        if p == "":
            return True
        try:
            value = int(p)
            return value > 0
        except ValueError:
            return False

    def load_and_prepare_seal_image(self):
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
        except NameError:
            script_dir = os.getcwd()
        seal_image_abs_path = os.path.join(script_dir, SEAL_IMAGE_PATH)
        if not os.path.exists(seal_image_abs_path):
            print(f"原本画像 '{seal_image_abs_path}' が見つかりません。")
            return None
        try:
            seal_image_original = Image.open(seal_image_abs_path).convert("RGB")
            crop_box = (11, 32, 84, 105)
            return seal_image_original.crop(crop_box)
        except Exception as e:
            print(f"原本画像読み込みエラー: {e}")
            return None

    def perform_check(self):
        try:
            current_thresholds = [float(var.get()) for var in self.threshold_vars]
            current_check_interval_ms = int(float(self.check_interval_var.get())) * 1000
        except:
            self.root.after(1000, self.perform_check)
            return

        current_tile_image = get_image_from_url(TILE_URL)
        if not current_tile_image:
            self.status_var.set("エラー: タイル画像取得失敗")
            self.root.after(current_check_interval_ms, self.perform_check)
            return

        monitoring_area_coords = (0, 391, 73, 464)
        current_monitored_area = current_tile_image.crop(monitoring_area_coords)
        diff_percentage, diff_image = compare_images(self.seal_image, current_monitored_area)

        resized_realtime_image = current_monitored_area.resize(self.DISPLAY_IMAGE_SIZE, Image.LANCZOS)
        resized_diff_image = diff_image.resize(self.DISPLAY_IMAGE_SIZE, Image.LANCZOS)
        # 画像をTkinter用に変換
        realtime_tk = ImageTk.PhotoImage(resized_realtime_image)
        diff_tk = ImageTk.PhotoImage(resized_diff_image)

        # GUIの画像を更新（オブジェクトを保持）
        self.realtime_image_label.config(image=realtime_tk)
        self.realtime_image_label.image = realtime_tk
        self.diff_image_label.config(image=diff_tk)
        self.diff_image_label.image = diff_tk


        # レベル判定
        level_name = None
        for thr, name in reversed(list(zip(current_thresholds, [v[1] for v in VANDALISM_LEVELS]))):
            if diff_percentage >= thr:
                level_name = name
                break

        if level_name:
            self.status_label.config(foreground="red")
            self.status_var.set(f"!!!!!! 荒らしを検知 !!!!!! ({level_name}, 差分: {diff_percentage:.2f}%)")
        else:
            self.status_label.config(foreground="white")
            self.status_var.set(f"監視中... (差分: {diff_percentage:.2f}%)")

        # グラフ更新
        current_time = time.time() - self.start_time
        self.time_history.append(current_time)
        self.diff_history.append(diff_percentage)
        window_start_time = current_time - 60
        self.time_history = [t for t in self.time_history if t >= window_start_time]
        self.diff_history = self.diff_history[-len(self.time_history):]

        self.ax.clear()
        self.ax.plot(self.time_history, self.diff_history, color='green', label="差分")
        # レベル線描画
        for thr, name in zip(current_thresholds, [v[1] for v in VANDALISM_LEVELS]):
            self.ax.axhline(y=thr, linestyle='--', label=f"{name}しきい値")
        self.ax.set_title("差分パーセンテージの推移", color='white')
        self.ax.set_xlabel("時間 (秒)", color='white')
        self.ax.set_ylabel("差分 (%)", color='white')
        self.ax.set_ylim(0, 100)
        self.ax.set_xlim(max(0, window_start_time), current_time+5)
        self.ax.tick_params(axis='x', colors='white')
        self.ax.tick_params(axis='y', colors='white')
        for spine in self.ax.spines.values():
            spine.set_color('white')
        self.ax.legend(facecolor='black', edgecolor='white', labelcolor='white')
        self.canvas.draw()

        self.root.after(int(current_check_interval_ms), self.perform_check)

    def update_uptime_display(self):
        elapsed_time = int(time.time() - self.start_time)
        hours = elapsed_time // 3600
        minutes = (elapsed_time % 3600) // 60
        seconds = elapsed_time % 60
        self.uptime_var.set(f"{hours:02}:{minutes:02}:{seconds:02}")
        self.root.after(1000, self.update_uptime_display)

if __name__ == "__main__":
    root = tk.Tk()
    app = VandalismDetectorApp(root)
    root.mainloop()
