import tkinter as tk
from tkinter import ttk # 追加
from PIL import Image, ImageTk, ImageChops
import requests
import io
import time
import os
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# 日本語フォントの設定 (Windowsの場合、'Meiryo'などが利用可能)
plt.rcParams['font.family'] = 'Meiryo'
plt.rcParams['axes.unicode_minus'] = False # 負の符号を正しく表示

# --- 設定 ---
# 監視するタイルのURL
TILE_URL = "https://backend.wplace.live/files/s0/tiles/1819/806.png"
# 菊の紋章の原本画像のファイルパス
SEAL_IMAGE_PATH = "kiku.png"
# 変更を検知するしきい値（0〜100のパーセンテージ）。
CHANGE_THRESHOLD = 8.0
# チェック間隔（ミリ秒）
CHECK_INTERVAL_MS = 1000

def get_image_from_url(url):
    """URLから画像をダウンロードし、PillowのImageオブジェクトとして返す"""
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return Image.open(io.BytesIO(response.content)).convert("RGB")
    except requests.exceptions.RequestException as e:
        print(f"画像のダウンロード中にエラーが発生しました: {e}")
        return None

def compare_images(img1, img2):
    """2つの画像を比較し、異なるピクセルの割合（%）と差分画像を返す"""
    if img1.size != img2.size:
        # サイズが違う場合、小さい方に合わせてクロップ（フォールバック）
        min_width = min(img1.width, img2.width)
        min_height = min(img1.height, img2.height)
        img1 = img1.crop((0, 0, min_width, min_height))
        img2 = img2.crop((0, 0, min_width, min_height))

    diff = ImageChops.difference(img1, img2)
    if diff.getbbox() is None:
        return 0.0, diff

    diff_non_zero = 0
    for pixel in diff.getdata():
        if pixel != (0, 0, 0):
            diff_non_zero += 1
            
    total_pixels = img1.width * img1.height
    if total_pixels == 0:
        return 0.0, diff
    
    percentage_diff = (diff_non_zero / total_pixels) * 100
    return percentage_diff, diff

class VandalismDetectorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("wplace 荒らし検出")
        self.root.attributes('-fullscreen', True) # 最初から全画面表示
        self.root.configure(bg='black') # 背景色を黒に設定

        # ttkスタイルを適用
        style = ttk.Style()
        style.theme_use('clam') # 'clam', 'alt', 'default', 'classic' など
        # 黒背景に合うようにスタイルを調整
        style.configure('TLabel', background='#1a1a1a', foreground='#e0e0e0', font=("Helvetica", 14))
        style.configure('TFrame', background='#1a1a1a')
        style.configure('TButton', background='#333333', foreground='#e0e0e0', font=("Helvetica", 12), borderwidth=0, focusthickness=3, focuscolor='#007bff') # ボタンのスタイル
        style.map('TButton', background=[('active', '#555555')], foreground=[('active', 'white')]) # ボタンのアクティブ時の色
        style.configure('TEntry', fieldbackground='#333333', foreground='#e0e0e0', insertbackground='#e0e0e0', borderwidth=1, relief="flat") # エントリーのスタイル
        style.map('TEntry', fieldbackground=[('focus', '#444444')])

        # スクロールバーのスタイル (もし将来的に必要になった場合)
        style.configure('TScrollbar', troughcolor='#333333', background='#555555', borderwidth=0)
        style.map('TScrollbar', background=[('active', '#777777')])

        # 状態表示ラベルのスタイル
        style.configure('Status.TLabel', background='#1a1a1a', foreground='#00ff00', font=("Helvetica", 20, "bold"), padding=15)
        style.map('Status.TLabel', foreground=[('active', '#00ff00'), ('!disabled', '#00ff00')], background=[('active', '#1a1a1a'), ('!disabled', '#1a1a1a')])

        # 画像フレーム内のラベルスタイル
        style.configure('ImageTitle.TLabel', background='#1a1a1a', foreground='#e0e0e0', font=("Helvetica", 16, "bold"))

        # 情報表示フレーム内のラベルスタイル
        style.configure('Info.TLabel', background='#1a1a1a', foreground='#e0e0e0', font=("Helvetica", 12))

        self.seal_image = self.load_and_prepare_seal_image()
        if not self.seal_image:
            root.destroy()
            return

        self.DISPLAY_IMAGE_SIZE = (450, 450) # 表示する画像のサイズをさらに大きく (73*約6.16)

        # パラメータ用のStringVarを初期化 (Entry用なのでStringVar)
        self.change_threshold_var = tk.StringVar(value=str(CHANGE_THRESHOLD))
        self.check_interval_var = tk.StringVar(value=str(CHECK_INTERVAL_MS // 1000)) # 秒単位で扱う

        # 追加情報表示用のStringVarを初期化
        self.last_detection_time_var = tk.StringVar(value="なし")
        self.total_detections_var = tk.IntVar(value=0)
        self.current_tile_url_var = tk.StringVar(value=TILE_URL)
        self.seal_image_path_var = tk.StringVar(value="読み込み中...")
        self.uptime_var = tk.StringVar(value="00:00:00")
        self.start_time = time.time() # 稼働時間計算用

        # グラフ用の設定
        self.diff_history = []
        self.time_history = []
        self.fig, self.ax = plt.subplots(figsize=(5, 3), dpi=100)
        self.ax.set_title("差分パーセンテージの推移")
        self.ax.set_xlabel("時間 (秒)")
        self.ax.set_ylabel("差分 (%)")
        self.ax.set_ylim(0, 100) # 0%から100%の範囲で固定
        self.ax.set_facecolor('black') # グラフの背景色を黒に
        self.fig.patch.set_facecolor('black') # Figureの背景色も黒に
        self.ax.tick_params(axis='x', colors='white') # x軸の目盛り色を白に
        self.ax.tick_params(axis='y', colors='white') # y軸の目盛り色を白に
        self.ax.spines['bottom'].set_color('white') # 下の枠線を白に
        self.ax.spines['top'].set_color('white') # 上の枠線を白に
        self.ax.spines['right'].set_color('white') # 右の枠線を白に
        self.ax.spines['left'].set_color('white') # 左の枠線を白に
        self.ax.title.set_color('white') # タイトル色を白に
        self.ax.xaxis.label.set_color('white') # x軸ラベル色を白に
        self.ax.yaxis.label.set_color('white') # y軸ラベル色を白に

        self.status_var = tk.StringVar()
        self.status_var.set("初期化中...")

        self.setup_gui() # GUIのセットアップを呼び出す
        self.perform_check() # 最初のチェックを開始
        self.update_uptime_display() # 稼働時間表示の更新を開始

    def setup_gui(self):
        """GUIのウィジェットをセットアップする"""
        # グリッドレイアウトの設定
        self.root.grid_rowconfigure(0, weight=0) # ステータスラベルは固定
        self.root.grid_rowconfigure(1, weight=1) # 画像フレームはリサイズに追従
        self.root.grid_rowconfigure(2, weight=0) # パラメータフレームは固定
        self.root.grid_rowconfigure(3, weight=0) # 情報表示フレームは固定
        self.root.grid_columnconfigure(0, weight=1) # 中央に配置

        self.status_label = ttk.Label(self.root, textvariable=self.status_var, style='Status.TLabel')
        self.status_label.grid(row=0, column=0, pady=10)

        self.image_frame = ttk.Frame(self.root, padding=10)
        self.image_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)
        
        # image_frame内のグリッド設定
        self.image_frame.grid_columnconfigure(0, weight=1)
        self.image_frame.grid_columnconfigure(1, weight=1)
        self.image_frame.grid_columnconfigure(2, weight=1) # グラフ用の新しいカラム
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

        # プレースホルダー画像を新しいサイズで作成
        placeholder = ImageTk.PhotoImage(Image.new('RGB', self.DISPLAY_IMAGE_SIZE, 'gray'))
        self.realtime_image_label.config(image=placeholder)
        self.diff_image_label.config(image=placeholder)
        self.realtime_image_label.image = placeholder
        self.diff_image_label.image = placeholder

        # グラフ表示用のフレーム
        self.graph_frame = ttk.Frame(self.image_frame)
        self.graph_frame.grid(row=0, column=2, sticky="nsew", padx=10, pady=10) # 新しいカラムを追加
        ttk.Label(self.graph_frame, text="差分グラフ", style='ImageTitle.TLabel').pack()
        
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.graph_frame)
        self.canvas_widget = self.canvas.get_tk_widget()
        self.canvas_widget.pack(side=tk.TOP, fill=tk.BOTH, expand=1)
        self.canvas.draw()

        # パラメータ調整用のフレーム
        self.param_frame = ttk.Frame(self.root, padding=10)
        self.param_frame.grid(row=2, column=0, pady=10)
        self.param_frame.grid_columnconfigure(0, weight=1)
        self.param_frame.grid_columnconfigure(1, weight=1)
        self.param_frame.grid_columnconfigure(2, weight=1)
        self.param_frame.grid_columnconfigure(3, weight=1)

        # しきい値入力
        ttk.Label(self.param_frame, text="しきい値 (%):", style='Info.TLabel').grid(row=0, column=0, sticky="w")
        self.threshold_entry = ttk.Entry(self.param_frame, textvariable=self.change_threshold_var, width=10)
        self.threshold_entry.grid(row=0, column=1, sticky="w")
        # 入力検証コマンドを設定
        vcmd_threshold = (self.root.register(self.validate_threshold_input), '%P')
        self.threshold_entry.config(validate="key", validatecommand=vcmd_threshold)

        # チェック間隔入力
        ttk.Label(self.param_frame, text="チェック間隔 (秒):").grid(row=1, column=0, sticky="w")
        self.interval_entry = ttk.Entry(self.param_frame, textvariable=self.check_interval_var, width=10)
        self.interval_entry.grid(row=1, column=1, sticky="w")
        # 入力検証コマンドを設定
        vcmd_interval = (self.root.register(self.validate_interval_input), '%P')
        self.interval_entry.config(validate="key", validatecommand=vcmd_interval)

        # 情報表示用のフレーム
        self.info_frame = ttk.Frame(self.root, padding=10)
        self.info_frame.grid(row=3, column=0, pady=10)
        self.info_frame.grid_columnconfigure(0, weight=1)
        self.info_frame.grid_columnconfigure(1, weight=1)

        ttk.Label(self.info_frame, text="稼働時間:", style='Info.TLabel').grid(row=0, column=0, sticky="w")
        ttk.Label(self.info_frame, textvariable=self.uptime_var, style='Info.TLabel').grid(row=0, column=1, sticky="w")

        # 制作者情報
        ttk.Label(self.root, text="Wplace皇居荒らし監視tool　　制作者:AI(Gemini)とGOLD", style='Info.TLabel').grid(row=4, column=0, pady=5)
        self.info_frame.grid_columnconfigure(2, weight=1)
        self.info_frame.grid_columnconfigure(3, weight=1)

        ttk.Label(self.info_frame, text="稼働時間:").grid(row=0, column=0, sticky="w")
        ttk.Label(self.info_frame, textvariable=self.uptime_var).grid(row=0, column=1, sticky="w")

    def validate_threshold_input(self, p):
        # しきい値入力の検証 (0-100の浮動小数点数)
        if p == "":
            return True # 空欄は許可 (後でデフォルト値が適用されるため)
        try:
            value = float(p)
            return 0.0 <= value <= 100.0
        except ValueError:
            return False

    def validate_interval_input(self, p):
        # チェック間隔入力の検証 (正の整数)
        if p == "":
            return True # 空欄は許可
        try:
            value = int(p)
            return value > 0
        except ValueError:
            return False

    def load_and_prepare_seal_image(self):
        """原本画像を読み込み、補正する"""
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
        except NameError:
            script_dir = os.getcwd()
        
        seal_image_abs_path = os.path.join(script_dir, SEAL_IMAGE_PATH)

        if not os.path.exists(seal_image_abs_path):
            print(f"エラー: 原本画像 '{seal_image_abs_path}' が見つかりません。")
            print(f"スクリプトと同じフォルダに '{SEAL_IMAGE_PATH}' を置いてください。")
            return None
        try:
            seal_image_original = Image.open(seal_image_abs_path).convert("RGB")
            crop_box = (11, 32, 84, 105)
            seal_image = seal_image_original.crop(crop_box)
            print("原本画像の読み込みと補正に成功しました。")
            return seal_image
        except Exception as e:
            print(f"原本画像の読み込みエラー: {e}")
            return None

    def update_threshold_label(self, value):
        self.threshold_label.config(text=f"{float(value):.1f}")

    def update_interval_label(self, value):
        self.interval_label.config(text=f"{int(float(value))}")

    def perform_check(self):
        """画像の取得、比較、GUIの更新を行う"""
        # スライダーから現在のしきい値とチェック間隔を取得
        current_threshold = float(self.change_threshold_var.get())
        current_check_interval_ms = int(float(self.check_interval_var.get())) * 1000 # 秒をミリ秒に変換

        current_tile_image = get_image_from_url(TILE_URL)
        if not current_tile_image:
            self.status_var.set("エラー: タイル画像取得失敗")
            self.root.after(current_check_interval_ms, self.perform_check)
            return

        monitoring_area_coords = (0, 391, 73, 464)
        current_monitored_area = current_tile_image.crop(monitoring_area_coords)
        
        diff_percentage, diff_image = compare_images(self.seal_image, current_monitored_area)

        # 画像をリサイズしてからTkinter用に変換
        # Image.LANCZOS は高品質なリサイズアルゴリズム
        resized_realtime_image = current_monitored_area.resize(self.DISPLAY_IMAGE_SIZE, Image.LANCZOS)
        resized_diff_image = diff_image.resize(self.DISPLAY_IMAGE_SIZE, Image.LANCZOS)

        realtime_tk = ImageTk.PhotoImage(resized_realtime_image)
        diff_tk = ImageTk.PhotoImage(resized_diff_image)

        # GUIの画像を更新
        self.realtime_image_label.config(image=realtime_tk)
        self.realtime_image_label.image = realtime_tk
        self.diff_image_label.config(image=diff_tk)
        self.diff_image_label.image = diff_tk

        # ステータスを更新
        if diff_percentage > current_threshold:
            self.status_label.config(foreground="red")
            self.status_var.set(f"!!!!!! 荒らしを検知 !!!!!! ({diff_percentage:.2f}%) ")
        else:
            self.status_label.config(foreground="white") # 黒背景なので白文字
            self.status_var.set(f"監視中... (差分: {diff_percentage:.2f}%)")

        # グラフデータを更新
        current_time = time.time() - self.start_time
        self.time_history.append(current_time)
        self.diff_history.append(diff_percentage)

        # 過去60秒間のデータのみ表示
        window_start_time = current_time - 60
        self.time_history = [t for t in self.time_history if t >= window_start_time]
        self.diff_history = self.diff_history[-len(self.time_history):] # time_historyと同期

        self.ax.clear()
        self.ax.plot(self.time_history, self.diff_history, color='green')
        self.ax.set_title("差分パーセンテージの推移", color='white')
        self.ax.set_xlabel("時間 (秒)", color='white')
        self.ax.set_ylabel("差分 (%)", color='white')
        self.ax.set_ylim(0, 100)
        self.ax.set_xlim(max(0, window_start_time), current_time + 5) # 現在時刻より少し先まで表示
        self.ax.tick_params(axis='x', colors='white')
        self.ax.tick_params(axis='y', colors='white')
        self.ax.spines['bottom'].set_color('white')
        self.ax.spines['top'].set_color('white')
        self.ax.spines['right'].set_color('white')
        self.ax.spines['left'].set_color('white')
        self.canvas.draw()

        # 次のチェックを予約
        self.root.after(int(current_check_interval_ms), self.perform_check)

    def update_uptime_display(self):
        # 稼働時間を更新して表示
        elapsed_time = int(time.time() - self.start_time)
        hours = elapsed_time // 3600
        minutes = (elapsed_time % 3600) // 60
        seconds = elapsed_time % 60
        self.uptime_var.set(f"{hours:02}:{minutes:02}:{seconds:02}")
        self.root.after(1000, self.update_uptime_display) # 1秒ごとに更新

if __name__ == "__main__":
    root = tk.Tk()
    app = VandalismDetectorApp(root)
    root.mainloop()
