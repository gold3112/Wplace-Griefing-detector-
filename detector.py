import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk, ImageChops, ImageOps, ImageEnhance
import requests
import io
import time
import os
import threading
import datetime as dt
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# ---- 表示用フォント（Windowsなら Meiryo が無難） ----
plt.rcParams['font.family'] = 'Meiryo'
plt.rcParams['axes.unicode_minus'] = False

# ---- 設定 ----
TILE_URL = "https://backend.wplace.live/files/s0/tiles/1819/806.png"
SEAL_IMAGE_PATH = "kiku.png"
CHECK_INTERVAL_MS = 1000

# 監視領域（タイル内座標）とテンプレ領域（原本内）
MONITOR_CROP = (0, 391, 73, 464)    # w=73, h=73
SEAL_CROP    = (11, 32, 84, 105)    # w=73, h=73

# レベル別しきい値初期値（％）
# 閾値が低い順に並べてください
VANDALISM_LEVELS = [
    (25.0, "軽度"),
    (50.0, "中度"),
    (75.0, "重度")
]

# ---- ユーティリティ ----
def get_image_from_url(url):
    """URLから画像を取得してPIL Imageオブジェクトとして返す"""
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        return Image.open(io.BytesIO(r.content)).convert("RGB")
    except requests.exceptions.RequestException as e:
        print(f"[network] 画像取得エラー: {e}")
        return None
    except OSError as e:
        print(f"[decode] 画像デコードエラー: {e}")
        return None

def compare_images(img1, img2):
    """差分画像と異なる画素割合[%]を返す。"""
    if img1.size != img2.size:
        w = min(img1.width, img2.width)
        h = min(img1.height, img2.height)
        img1 = img1.crop((0, 0, w, h))
        img2 = img2.crop((0, 0, w, h))
    diff = ImageChops.difference(img1, img2)
    bbox = diff.getbbox()
    if not bbox:
        return 0.0, diff
    nonzero = sum(1 for p in diff.getdata() if p != (0, 0, 0))
    return (nonzero / (diff.width * diff.height)) * 100.0, diff

# ---- アプリ ----
class VandalismDetectorApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("wplace 荒らし検出")

        # フルスクリーン管理
        self.is_fullscreen = False
        self.windowed_geometry = '1280x800'
        self.root.geometry(self.windowed_geometry)
        self.root.bind('<F11>', self.toggle_fullscreen)
        self.root.bind('<Alt-Return>', self.toggle_fullscreen)
        self.root.bind('<Escape>', self.exit_fullscreen)

        # 集中表示（UI最小化）
        self.focus_mode = False
        self.root.bind('<F2>', self.toggle_focus_mode)

        # 状態
        self.monitoring = True
        self.start_time = time.time()
        self.last_snapshot = None

        # 監視用ベースサイズ
        self.base_w = MONITOR_CROP[2] - MONITOR_CROP[0]
        self.base_h = MONITOR_CROP[3] - MONITOR_CROP[1]

        # 画質関連の設定値
        self.scale_preference = tk.IntVar(value=8)  # ユーザー希望倍率（1〜20）
        self.render_mode_var = tk.StringVar(value='Pixel-Perfect')
        self.enhance_diff_var = tk.BooleanVar(value=True)

        # 検知パラメータ
        self.threshold_vars = [tk.StringVar(value=str(v[0])) for v in VANDALISM_LEVELS]
        self.check_interval_var = tk.StringVar(value=str(CHECK_INTERVAL_MS // 1000))

        # 表示・統計
        self.uptime_var = tk.StringVar(value='00:00:00')
        self.status_var = tk.StringVar(value='初期化中...')
        self.status_color = '#00c853'
        self.total_detections = 0
        self.last_detection_time = tk.StringVar(value='なし')

        # グラフ準備
        self.diff_history = []
        self.time_history = []
        self.fig, self.ax = plt.subplots(figsize=(5.4, 3.2), dpi=100)
        self._style_matplotlib()
        self.canvas_mpl = None

        # 画像プレースホルダ & 最新イメージ保持（リサイズ再描画用）
        self.photo_realtime = None
        self.photo_diff = None
        self._last_area = None
        self._last_diff = None

        # テンプレ画像
        self.seal_image = self._load_seal()
        if not self.seal_image:
            self._fatal("原本 kiku.png が見つかりません。スクリプトと同じフォルダに配置してください。")
            return

        # UI 構築
        self._build_styles()
        self._build_ui()

        # リサイズに応じた再描画
        self.root.bind('<Configure>', self._on_any_configure)

        # 監視ループ開始
        self.root.after(200, self._tick_uptime)
        self.root.after(0, self.perform_check)

    # ---------- UI 構築 ----------
    def _build_styles(self):
        style = ttk.Style()
        try:
            style.theme_use('clam')
        except tk.TclError:
            pass
        bg = '#0f1115'; fg = '#e6e6e6'; acc = '#2d333b'; green = '#00c853'; red = '#ff5252'
        self.COLORS = dict(bg=bg, fg=fg, acc=acc, green=green, red=red)
        self.root.configure(bg=bg)
        style.configure('.', background=bg, foreground=fg)
        style.configure('TFrame', background=bg)
        style.configure('TLabel', background=bg, foreground=fg)
        style.configure('Header.TLabel', font=('Segoe UI', 14, 'bold'))
        style.configure('Status.TLabel', font=('Segoe UI', 12))
        style.configure('Title.TLabel', font=('Segoe UI', 16, 'bold'))
        style.configure('Pct.TLabel', font=('Segoe UI', 28, 'bold'))
        style.configure('PctBig.TLabel', font=('Segoe UI', 44, 'bold'))
        style.configure('Card.TFrame', background=acc)
        style.configure('CardTitle.TLabel', background=acc, foreground=fg, font=('Segoe UI', 12, 'bold'))
        style.configure('TEntry', fieldbackground=acc)
        style.configure('TCheckbutton', background=bg, foreground=fg)

    def _build_ui(self):
        # ヘッダー
        self.header = ttk.Frame(self.root, padding=(12, 8))
        self.header.grid(row=0, column=0, sticky='ew')
        self.header.grid_columnconfigure(1, weight=1)
        ttk.Label(self.header, text='wplace 荒らし検出', style='Title.TLabel').grid(row=0, column=0, sticky='w')
        self.dot = ttk.Label(self.header, text='●', foreground=self.status_color, style='Title.TLabel')
        self.dot.grid(row=0, column=1, sticky='e', padx=(0, 8))
        ttk.Button(self.header, text='全画面 (F11)', command=self.toggle_fullscreen).grid(row=0, column=2, padx=4)
        ttk.Button(self.header, text='集中表示 (F2)', command=self.toggle_focus_mode).grid(row=0, column=3, padx=4)

        # メイン
        self.main = ttk.Frame(self.root, padding=10)
        self.main.grid(row=1, column=0, sticky='nsew')
        self.root.grid_rowconfigure(1, weight=1)
        self.root.grid_columnconfigure(0, weight=1)
        self.main.grid_columnconfigure(0, weight=1)
        self.main.grid_columnconfigure(1, weight=1)
        self.main.grid_columnconfigure(2, weight=1)
        self.main.grid_rowconfigure(1, weight=1)

        # カード: リアルタイム
        card_rt = self._card(self.main, 'リアルタイム', row=1, col=0)
        self.rt_container = ttk.Frame(card_rt, style='TFrame')
        self.rt_container.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self.lbl_rt = ttk.Label(self.rt_container)
        self.lbl_rt.pack(expand=True)

        # カード: 差分
        card_df = self._card(self.main, '差分', row=1, col=1)
        self.df_container = ttk.Frame(card_df, style='TFrame')
        self.df_container.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self.lbl_diff = ttk.Label(self.df_container)
        self.lbl_diff.pack(expand=True)

        # カード: グラフ
        card_gr = self._card(self.main, '差分グラフ', row=1, col=2)
        self.canvas_mpl = FigureCanvasTkAgg(self.fig, master=card_gr)
        self.canvas_mpl.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self.canvas_mpl.draw()

        # 操作パネル（折りたたみ対象）
        self.panel = ttk.Frame(self.main, padding=(8, 4))
        self.panel.grid(row=0, column=0, columnspan=3, sticky='ew')
        
        # 荒らしレベル設定
        for i, (thr, name) in enumerate(VANDALISM_LEVELS):
            ttk.Label(self.panel, text=f'{name} (%)').grid(row=0, column=i*2, sticky='w')
            ent = ttk.Entry(self.panel, textvariable=self.threshold_vars[i], width=6)
            ent.grid(row=0, column=i*2+1, sticky='w', padx=(4, 12))
            ent.config(validate='key', validatecommand=(self.root.register(self._vc_float_0_100), '%P'))
            
        ttk.Label(self.panel, text='間隔(秒)').grid(row=0, column=len(VANDALISM_LEVELS)*2, sticky='w')
        self.ent_iv = ttk.Entry(self.panel, textvariable=self.check_interval_var, width=6)
        self.ent_iv.grid(row=0, column=len(VANDALISM_LEVELS)*2+1, sticky='w', padx=(4, 12))
        self.ent_iv.config(validate='key', validatecommand=(self.root.register(self._vc_pos_int), '%P'))
        
        ttk.Label(self.panel, text='拡大希望').grid(row=1, column=0, sticky='w')
        self.scl_scale = ttk.Scale(self.panel, from_=1, to=20, orient='horizontal', command=self._on_scale_change)
        self.scl_scale.set(self.scale_preference.get())
        self.scl_scale.grid(row=1, column=1, columnspan=len(VANDALISM_LEVELS)*2+1, sticky='ew', padx=(4, 12))
        
        ttk.Label(self.panel, text='描画').grid(row=2, column=0, sticky='w')
        self.cmb_render = ttk.Combobox(self.panel, state='readonly', values=['Pixel-Perfect', 'Smooth'])
        self.cmb_render.set(self.render_mode_var.get())
        self.cmb_render.bind('<<ComboboxSelected>>', lambda e: self.render_mode_var.set(self.cmb_render.get()))
        self.cmb_render.grid(row=2, column=1, sticky='w', padx=(4, 12))
        
        self.chk_enh = ttk.Checkbutton(self.panel, text='差分強調', variable=self.enhance_diff_var)
        self.chk_enh.grid(row=2, column=2, sticky='w')

        # ステータスバー（折りたたみ対象）
        self.status = ttk.Frame(self.root, padding=(12, 6))
        self.status.grid(row=2, column=0, sticky='ew')
        self.status.grid_columnconfigure(1, weight=1)
        self.lbl_status = ttk.Label(self.status, textvariable=self.status_var, style='Status.TLabel')
        self.lbl_status.grid(row=0, column=0, sticky='w')
        ttk.Label(self.status, text='稼働:').grid(row=0, column=2, sticky='e', padx=(12, 2))
        ttk.Label(self.status, textvariable=self.uptime_var).grid(row=0, column=3, sticky='e')
        ttk.Label(self.status, text=' / 検知回数:').grid(row=0, column=4, sticky='e', padx=(8, 2))
        self.lbl_cnt = ttk.Label(self.status, text=str(self.total_detections))
        self.lbl_cnt.grid(row=0, column=5, sticky='e')
        ttk.Label(self.status, text=' / 最終検知:').grid(row=0, column=6, sticky='e', padx=(8, 2))
        ttk.Label(self.status, textvariable=self.last_detection_time).grid(row=0, column=7, sticky='e')
        
        ttk.Label(self.root, text=r"制作者 : GOLD.add(ゴリ).append(鍵は掛けとこうね) + [ChatGPT, Gemini]", style='TLabel').grid(row=3, column=0, pady=5)


        # 差分%の大型オーバーレイ（トップ中央）
        self.overlay = ttk.Label(self.root, text='0.00 %', style='Pct.TLabel')
        self.overlay.configure(foreground='#ffffff')
        self.overlay.place(in_=self.main, relx=0.5, rely=0.02, anchor='n')

    def _card(self, parent, title, row, col):
        frame = ttk.Frame(parent, style='Card.TFrame', padding=6)
        frame.grid(row=row, column=col, sticky='nsew', padx=6, pady=6)
        parent.grid_rowconfigure(row, weight=1)
        parent.grid_columnconfigure(col, weight=1)
        ttk.Label(frame, text=title, style='CardTitle.TLabel').pack(anchor='w', padx=6, pady=(2, 0))
        return frame

    def _style_matplotlib(self):
        self.ax.set_title("差分パーセンテージの推移", color='white')
        self.ax.set_xlabel("時間 (秒)", color='white')
        self.ax.set_ylabel("差分 (%)", color='white')
        self.ax.set_ylim(0, 100)
        self.ax.set_facecolor('#0f1115')
        self.fig.patch.set_facecolor('#0f1115')
        for s in self.ax.spines.values():
            s.set_color('white')
        self.ax.tick_params(axis='x', colors='white')
        self.ax.tick_params(axis='y', colors='white')
        self.ax.grid(color='#444444', linestyle=':', linewidth=0.5)

    # ---------- 機能 ----------
    def _load_seal(self):
        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))
        except NameError:
            base_dir = os.getcwd()
        path = os.path.join(base_dir, SEAL_IMAGE_PATH)
        if not os.path.exists(path):
            print(f"[file] 原本が見つかりません: {path}")
            return None
        try:
            img = Image.open(path).convert('RGB')
            return img.crop(SEAL_CROP)
        except Exception as e:
            print(f"[file] 原本読み込みエラー: {e}")
            return None

    def toggle_fullscreen(self, event=None):
        self.is_fullscreen = not self.is_fullscreen
        if self.is_fullscreen:
            self.windowed_geometry = self.root.geometry()
            self.root.attributes('-fullscreen', True)
        else:
            self.root.attributes('-fullscreen', False)
            if self.windowed_geometry:
                self.root.geometry(self.windowed_geometry)
        self.root.after(10, self._render_current) # 描画遅延に対応
        return 'break'

    def exit_fullscreen(self, event=None):
        if self.is_fullscreen:
            self.is_fullscreen = False
            self.root.attributes('-fullscreen', False)
            if self.windowed_geometry:
                self.root.geometry(self.windowed_geometry)
        self.root.after(10, self._render_current)
        return 'break'

    def toggle_focus_mode(self, event=None):
        self.focus_mode = not self.focus_mode
        if self.focus_mode:
            self.header.grid_remove()
            self.panel.grid_remove()
            self.status.grid_remove()
            self.root.grid_rowconfigure(1, weight=1)
            self.overlay.configure(style='PctBig.TLabel')
        else:
            self.header.grid()
            self.panel.grid()
            self.status.grid()
            self.root.grid_rowconfigure(1, weight=1)
            self.overlay.configure(style='Pct.TLabel')
        self.root.after(10, self._render_current)
        return 'break'

    def _set_status_color(self, color):
        if hasattr(self, 'dot'):
            self.dot.configure(foreground=color)

    def _tick_uptime(self):
        elapsed = int(time.time() - self.start_time)
        h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
        self.uptime_var.set(f"{h:02}:{m:02}:{s:02}")
        self.root.after(1000, self._tick_uptime)

    def _vc_float_0_100(self, p):
        if p == '':
            return True
        try:
            v = float(p)
            return 0.0 <= v <= 100.0
        except ValueError:
            return False

    def _vc_pos_int(self, p):
        if p == '':
            return True
        try:
            return int(p) > 0
        except ValueError:
            return False

    def _on_scale_change(self, _):
        self.scale_preference.set(int(float(self.scl_scale.get())))
        self._render_current()

    # ---------- レイアウト変化での再描画 ----------
    def _on_any_configure(self, event):
        self.root.after(10, self._render_current)

    def _effective_disp_size(self):
        w_rt = max(1, self.rt_container.winfo_width() - 16)
        h_rt = max(1, self.rt_container.winfo_height() - 16)
        w_df = max(1, self.df_container.winfo_width() - 16)
        h_df = max(1, self.df_container.winfo_height() - 16)
        w = max(1, min(w_rt, w_df))
        h = max(1, min(h_rt, h_df))

        sx = w / self.base_w
        sy = h / self.base_h
        s = max(1.0, min(sx, sy))
        s = min(s, float(self.scale_preference.get()))

        if self.render_mode_var.get() == 'Pixel-Perfect':
            s = max(1, int(s))
        
        disp_w = max(1, int(self.base_w * s))
        disp_h = max(1, int(self.base_h * s))
        return (disp_w, disp_h)

    # ---------- 監視ループ（非ブロッキング） ----------
    def perform_check(self):
        try:
            interval_ms = int(float(self.check_interval_var.get())) * 1000
            if interval_ms <= 0:
                interval_ms = 1000
        except (ValueError, IndexError):
            interval_ms = 1000

        if self.monitoring:
            threading.Thread(target=self._fetch_and_update, daemon=True).start()
        self.root.after(interval_ms, self.perform_check)

    def _fetch_and_update(self):
        tile = get_image_from_url(TILE_URL)
        if tile is None:
            self.root.after(0, lambda: self.status_var.set('エラー: タイル画像取得失敗'))
            return

        area = tile.crop(MONITOR_CROP)
        diff_pct, diff_img = compare_images(self.seal_image, area)
        self._last_area = area
        self._last_diff = diff_img

        self.root.after(0, lambda: self._update_ui(diff_pct))

    # ---------- 画像更新 & グラフ ----------
    def _render_current(self):
        if self._last_area is None or self._last_diff is None:
            return
        
        disp_size = self._effective_disp_size()
        mode = self.render_mode_var.get()
        resample = Image.NEAREST if mode == 'Pixel-Perfect' else Image.LANCZOS

        shown_rt = self._last_area.resize(disp_size, resample)
        shown_df = self._last_diff
        if self.enhance_diff_var.get():
            shown_df = ImageOps.autocontrast(shown_df, cutoff=2)
            shown_df = ImageEnhance.Contrast(shown_df).enhance(1.4)
        shown_df = shown_df.resize(disp_size, resample)

        self.photo_realtime = ImageTk.PhotoImage(shown_rt)
        self.photo_diff = ImageTk.PhotoImage(shown_df)
        self.lbl_rt.configure(image=self.photo_realtime)
        self.lbl_diff.configure(image=self.photo_diff)

    def _update_ui(self, diff_pct: float):
        self._render_current()
        self.overlay.configure(text=f"{diff_pct:.2f} %")

        # 荒らしレベル判定
        level_name = "監視中"
        status_msg = f"監視中... (差分: {diff_pct:.2f}%)"
        status_color = self.COLORS['green']
        overlay_color = '#ffffff'
        is_detection = False
        
        try:
            thresholds = sorted([float(v.get()) for v in self.threshold_vars])
        except ValueError:
            thresholds = sorted([v[0] for v in VANDALISM_LEVELS])

        for thr, name in reversed(list(zip(thresholds, [v[1] for v in VANDALISM_LEVELS]))):
            if diff_pct >= thr:
                level_name = name
                is_detection = True
                break
        
        if is_detection:
            status_msg = f"!!!!!! 荒らしを検知 !!!!!! ({level_name}, 差分: {diff_pct:.2f}%)"
            status_color = self.COLORS['red']
            overlay_color = '#ff5252'
            self.total_detections += 1
            self.lbl_cnt.configure(text=str(self.total_detections))
            self.last_detection_time.set(dt.datetime.now().strftime('%H:%M:%S'))
            
        self.status_var.set(status_msg)
        self._set_status_color(status_color)
        self.overlay.configure(foreground=overlay_color)

        # グラフ更新
        t = time.time() - self.start_time
        self.time_history.append(t)
        self.diff_history.append(diff_pct)
        t0 = t - 60
        while self.time_history and self.time_history[0] < t0:
            self.time_history.pop(0); self.diff_history.pop(0)
        self.ax.clear(); self._style_matplotlib()
        self.ax.plot(self.time_history, self.diff_history, color='cyan')
        
        # グラフにしきい値線を追加
        thresholds = sorted([float(v.get()) for v in self.threshold_vars], reverse=False)
        for thr, name in list(zip(thresholds, [v[1] for v in VANDALISM_LEVELS])):
            self.ax.axhline(y=thr, linestyle='--', color='red', alpha=0.5, label=f"{name}しきい値")

        self.ax.set_xlim(max(0, t0), t + 5)
        self.ax.legend(facecolor='#0f1115', edgecolor='white', labelcolor='white')
        self.canvas_mpl.draw()

    # ---------- 致命的エラー ----------
    def _fatal(self, msg):
        top = tk.Toplevel(self.root)
        top.title('エラー')
        ttk.Label(top, text=msg, foreground=self.COLORS['red']).pack(padx=20, pady=20)
        ttk.Button(top, text='閉じる', command=self.root.destroy).pack(pady=(0, 12))

if __name__ == '__main__':
    root = tk.Tk()
    app = VandalismDetectorApp(root)
    root.mainloop()