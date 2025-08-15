#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk, ImageChops
import requests, io, time, os
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import math 

# --- 定数設定 ---
# 監視開始位置: (タイルのx, タイルのy, タイル内のx, タイル内のy)
DEFAULT_REF_PIXEL = (1818, 806, 989, 359)
DEFAULT_SEAL_IMAGE_PATH = "kiku.png"
DEFAULT_INTERVAL_MS = 1000
WINDOW_GEOMETRY = "1200x800"
TILE_SIZE = 1000 # wplaceのタイルのサイズは1000x1000ピクセル

# 荒らしレベルと色の定義 (しきい値は変数で管理)
LEVELS_DATA = [
    {"label": "超大規模荒らし", "color": "#ff4d4f", "graph_color": "#c0392b", "default_limit": 36.0},
    {"label": "大規模荒らし", "color": "#ff7a45", "graph_color": "#d35400", "default_limit": 27.0},
    {"label": "中規模荒らし", "color": "#ffa940", "graph_color": "#f39c12", "default_limit": 15.0},
    {"label": "小規模荒らし", "color": "#40c057", "graph_color": "#27ae60", "default_limit": 6.1},
]
NORMAL_COLOR = "#e0e0e0"
NORMAL_GRAPH_COLOR = "#2ecc71"

class VandalismDetectorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("wplace 荒らし検出 v3.15 - バグ修正版")
        self.root.geometry(WINDOW_GEOMETRY)
        self.root.minsize(1000, 650)
        
        # --- 変数定義 ---
        self.diff_pct = 0.0
        self.start_time = time.time()
        self.current_cropped_image = None
        self.current_diff_image = None
        self.after_id = None
        self.diff_history = []
        self.time_history = []
        self.max_history_points = 100 
        self.seal_image = None
        self.original_image_width = 0
        self.original_image_height = 0
        self.monitor_size = (0, 0)

        # --- Tkinter変数 ---
        self.realtime_ref_pixel_var = tk.StringVar(value=f"{DEFAULT_REF_PIXEL[0]}, {DEFAULT_REF_PIXEL[1]}, {DEFAULT_REF_PIXEL[2]}, {DEFAULT_REF_PIXEL[3]}")
        self.interval_sec_var = tk.IntVar(value=max(1, DEFAULT_INTERVAL_MS // 1000))
        self.reference_image_path_var = tk.StringVar(value=DEFAULT_SEAL_IMAGE_PATH)
        self.status_var = tk.StringVar(value="初期化中...")
        
        # 閾値用のTkinter変数
        self.threshold_vars = [tk.DoubleVar(value=d['default_limit']) for d in LEVELS_DATA]

        # --- GUIの構築 ---
        self._setup_styles()
        self.root.configure(background=self.BG_COLOR)
        self._build_gui()
        
        # --- 初期設定の適用 ---
        self._apply_settings(initial_load=True)
        
        # --- 定期処理の開始 ---
        self._tick_check()
        self.root.bind("<Configure>", self._on_resize)

    def _setup_styles(self):
        """UIのスタイルを一括で設定します。"""
        self.BG_COLOR = "#1c1c1c"
        self.FG_COLOR = "#e0e0e0"
        self.CARD_BG = "#2a2a2a"
        self.ACCENT_COLOR = "#00b894"
        self.BORDER_COLOR = "#444444"
        
        font_family = "sans-serif"
        jp_fonts = ['Yu Gothic', 'Hiragino Sans', 'Noto Sans CJK JP', 'TakaoGothic', 'IPAexGothic']
        for font in jp_fonts:
            if fm.findfont(font, fallback_to_default=False):
                plt.rcParams['font.family'] = font
                font_family = font
                break
        else:
            print("Warning: No Japanese font found. Falling back to default.")

        plt.rcParams['axes.unicode_minus'] = False
        
        style = ttk.Style()
        style.theme_use("clam")
        
        style.configure(".", background=self.BG_COLOR, foreground=self.FG_COLOR, font=(font_family, 11))
        style.configure("TFrame", background=self.BG_COLOR)
        style.configure("Card.TFrame", background=self.CARD_BG, borderwidth=1, relief="solid")
        style.map("Card.TFrame", bordercolor=[("active", self.ACCENT_COLOR)])
        
        style.configure("TLabel", background=self.BG_COLOR, foreground=self.FG_COLOR)
        style.configure("Card.TLabel", background=self.CARD_BG)
        style.configure("Header.TLabel", font=(font_family, 24, "bold"), foreground="#ffffff")
        style.configure("SubHeader.TLabel", font=(font_family, 12, "bold"), foreground=self.ACCENT_COLOR)
        style.configure("Status.TLabel", font=(font_family, 20, "bold"))
        
        style.configure("TEntry", fieldbackground="#333333", foreground=self.FG_COLOR, bordercolor=self.BORDER_COLOR, insertcolor=self.FG_COLOR)
        style.map("TEntry", bordercolor=[("focus", self.ACCENT_COLOR)])
        
        style.configure("TButton", background=self.ACCENT_COLOR, foreground="#ffffff", font=(font_family, 11, "bold"), borderwidth=0)
        style.map("TButton", background=[("active", "#00d2a7")])

        self.font_family = font_family

    def _build_gui(self):
        header = ttk.Frame(self.root, padding=15)
        header.pack(fill="x", side="top")
        ttk.Label(header, text="VANDALISM DETECTOR", style="Header.TLabel").pack(side="left")

        main_pane = ttk.PanedWindow(self.root, orient="horizontal")
        main_pane.pack(expand=True, fill="both", padx=15, pady=(0, 15))

        left_frame = self._create_visual_frame(main_pane)
        main_pane.add(left_frame, weight=3)
        
        right_frame = self._create_settings_frame(main_pane)
        main_pane.add(right_frame, weight=1)

    def _create_visual_frame(self, parent):
        frame = ttk.Frame(parent)

        status_frame = ttk.Frame(frame, padding=(0, 0, 0, 10))
        status_frame.pack(fill="x", side="top")
        self.status_label = ttk.Label(status_frame, textvariable=self.status_var, style="Status.TLabel")
        self.status_label.pack(side="left", anchor="w")
        
        # グラフは下部に固定
        graph_frame = ttk.Frame(frame, style="Card.TFrame")
        graph_frame.pack(fill="both", pady=(20, 0), side="bottom")
        
        chart_container = ttk.Frame(graph_frame, style="Card.TFrame")
        chart_container.pack(expand=True, fill="both")
        chart_container.columnconfigure(0, weight=1)
        chart_container.columnconfigure(1, weight=3)
        
        self.pie_fig, self.pie_ax = plt.subplots(figsize=(3, 3), dpi=100)
        self.pie_fig.patch.set_facecolor(self.CARD_BG)
        self.pie_ax.set_facecolor(self.CARD_BG)
        self.pie_canvas = FigureCanvasTkAgg(self.pie_fig, master=chart_container)
        self.pie_canvas_widget = self.pie_canvas.get_tk_widget()
        self.pie_canvas_widget.configure(bg=self.CARD_BG)
        self.pie_canvas_widget.grid(row=0, column=0, padx=10, pady=5, sticky="nsew")

        self.line_fig, self.line_ax = plt.subplots(figsize=(4, 4), dpi=100)
        self.line_fig.patch.set_facecolor(self.CARD_BG)
        self.line_ax.set_facecolor(self.CARD_BG)
        self.line_canvas = FigureCanvasTkAgg(self.line_fig, master=chart_container)
        self.line_canvas_widget = self.line_canvas.get_tk_widget()
        self.line_canvas_widget.configure(bg=self.CARD_BG)
        self.line_canvas_widget.grid(row=0, column=1, padx=10, pady=5, sticky="nsew")
        
        self._update_graph(0)
        
        # 画像表示エリアをgridで分割
        image_area = ttk.Frame(frame)
        image_area.pack(expand=True, fill="both")
        image_area.columnconfigure(0, weight=1)
        image_area.columnconfigure(1, weight=1)
        image_area.rowconfigure(0, weight=1)

        # リアルタイム画像コンテナ
        realtime_container = ttk.Frame(image_area, borderwidth=1, relief="solid")
        realtime_container.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        ttk.Label(realtime_container, text="リアルタイム", style="SubHeader.TLabel").pack(anchor="w", padx=5, pady=5)
        self.realtime_image_label = ttk.Label(realtime_container)
        self.realtime_image_label.pack(expand=True, padx=5, pady=5)

        # 差分画像コンテナ
        diff_container = ttk.Frame(image_area, borderwidth=1, relief="solid")
        diff_container.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        ttk.Label(diff_container, text="差分", style="SubHeader.TLabel").pack(anchor="w", padx=5, pady=5)
        self.diff_image_label = ttk.Label(diff_container)
        self.diff_image_label.pack(expand=True, padx=5, pady=5)
        
        return frame

    def _create_settings_frame(self, parent):
        frame = ttk.Frame(parent, style="Card.TFrame", padding=20)
        
        ttk.Label(frame, text="設定", style="SubHeader.TLabel", background=self.CARD_BG).pack(anchor="w", pady=(0, 15))

        ttk.Label(frame, text="リアルタイム参照ピクセル\n(タイルx, タイルy, タイル内x, タイル内y)", style="Card.TLabel").pack(anchor="w", pady=(10, 2))
        ttk.Entry(frame, textvariable=self.realtime_ref_pixel_var).pack(fill="x")

        ttk.Label(frame, text="更新間隔 (秒)", style="Card.TLabel").pack(anchor="w", pady=(10, 2))
        ttk.Entry(frame, textvariable=self.interval_sec_var).pack(fill="x")
        
        ttk.Label(frame, text="参照元画像パス", style="Card.TLabel").pack(anchor="w", pady=(10, 2))
        ttk.Entry(frame, textvariable=self.reference_image_path_var).pack(fill="x")
        
        ttk.Label(frame, text="荒らしレベルの閾値 (%)", style="SubHeader.TLabel", background=self.CARD_BG).pack(anchor="w", pady=(20, 5))
        for var, data in zip(self.threshold_vars, LEVELS_DATA):
            label_frame = ttk.Frame(frame, style="Card.TFrame")
            label_frame.pack(fill="x", pady=2)
            ttk.Label(label_frame, text=data["label"], background=self.CARD_BG).pack(side="left", padx=(0, 10))
            ttk.Entry(label_frame, textvariable=var, width=5).pack(side="right")
        
        # 適用ボタンとリセットボタンを並べるフレーム
        button_frame = ttk.Frame(frame, style="Card.TFrame")
        button_frame.pack(fill="x", pady=(15, 0))
        button_frame.columnconfigure(0, weight=1)
        button_frame.columnconfigure(1, weight=1)

        ttk.Button(button_frame, text="デフォルトに戻す", command=self._reset_settings).grid(row=0, column=0, sticky="ew", padx=(0, 5))
        ttk.Button(button_frame, text="適用", command=self._apply_settings).grid(row=0, column=1, sticky="ew", padx=(5, 0))
        
        return frame
    
    def _apply_settings(self, initial_load=False):
        """設定を適用し、参照画像を再読み込みします。"""
        ref_pixel_quad = safe_int_quad(self.realtime_ref_pixel_var.get(), DEFAULT_REF_PIXEL)

        if ref_pixel_quad == "error":
            messagebox.showerror("設定適用失敗", "座標はカンマ区切りの4つの整数で入力してください。")
            if initial_load: self.root.destroy()
            return
        
        self.seal_image = self._load_reference()
        
        if self.seal_image:
            self.monitor_size = self.seal_image.size
            self.original_image_width = self.monitor_size[0]
            self.original_image_height = self.monitor_size[1]
            
            self._update_images_display()

            if not initial_load:
                messagebox.showinfo("設定適用", "設定が適用されました。")
        else:
            if initial_load: self.root.destroy()

    def _reset_settings(self):
        """設定をデフォルト値に戻します。"""
        self.realtime_ref_pixel_var.set(f"{DEFAULT_REF_PIXEL[0]}, {DEFAULT_REF_PIXEL[1]}, {DEFAULT_REF_PIXEL[2]}, {DEFAULT_REF_PIXEL[3]}")
        self.interval_sec_var.set(max(1, DEFAULT_INTERVAL_MS // 1000))
        self.reference_image_path_var.set(DEFAULT_SEAL_IMAGE_PATH)

        for var, data in zip(self.threshold_vars, LEVELS_DATA):
            var.set(data['default_limit'])
            
        # 参照画像を再読み込み
        self.seal_image = self._load_reference()
        
        if self.seal_image:
            self.monitor_size = self.seal_image.size
            self.original_image_width = self.monitor_size[0]
            self.original_image_height = self.monitor_size[1]
            self._update_images_display()
            messagebox.showinfo("設定リセット", "設定がデフォルト値に戻されました。")
        else:
            messagebox.showerror("設定リセット失敗", "参照画像の読み込みに失敗しました。")

    def _load_reference(self):
        """参照画像をトリミングせずに読み込みます。透過情報を保持します。"""
        path = self.reference_image_path_var.get()
        
        script_dir = os.path.dirname(os.path.abspath(__file__))
        abs_path = os.path.join(script_dir, path)
        if not os.path.exists(abs_path):
            messagebox.showerror("原本画像エラー", f"{abs_path} が見つかりません")
            return None
        
        try:
            full_ref_img = Image.open(abs_path).convert("RGBA")
            
            alpha_data = full_ref_img.getchannel('A').getdata()
            opaque_pixels_count = sum(1 for p in alpha_data if p > 0)
            total_pixels = full_ref_img.width * full_ref_img.height
            print(f"デバッグ情報: 参照元画像が読み込まれました。")
            print(f"  全体のピクセル数: {total_pixels}")
            print(f"  透過していない（監視対象）ピクセル数: {opaque_pixels_count}")
            
            return full_ref_img
        except Exception as e:
            messagebox.showerror("参照画像エラー", f"参照画像の読み込み中にエラーが発生しました: {e}")
            return None

    def _fetch_tiles_and_crop(self, tile_x, tile_y, x_in_tile, y_in_tile, width, height):
        """
        指定されたタイル座標とタイル内座標、サイズに基づいて
        必要なタイルを結合し、監視領域をクロップして返します。
        """
        try:
            base_url = "https://backend.wplace.live/files/s0/tiles"
            
            # --- TILE_SIZE に基づくグローバル座標の計算 ---
            global_x = tile_x * TILE_SIZE + x_in_tile
            global_y = tile_y * TILE_SIZE + y_in_tile
            
            # 監視領域がカバーするタイルの範囲を計算
            start_tile_x = global_x // TILE_SIZE
            start_tile_y = global_y // TILE_SIZE
            end_tile_x = (global_x + width - 1) // TILE_SIZE
            end_tile_y = (global_y + height - 1) // TILE_SIZE
            
            tile_images = {}
            for tx in range(start_tile_x, end_tile_x + 1):
                for ty in range(start_tile_y, end_tile_y + 1):
                    url = f"{base_url}/{tx}/{ty}.png"
                    img = get_image_from_url(url)
                    if img:
                        tile_images[(tx, ty)] = img.convert("RGBA")
            
            if not tile_images:
                print("デバッグ情報: タイルの取得に失敗しました。")
                return None

            # 結合画像のサイズを計算
            combined_width = (end_tile_x - start_tile_x + 1) * TILE_SIZE
            combined_height = (end_tile_y - start_tile_y + 1) * TILE_SIZE
            combined_img = Image.new("RGBA", (combined_width, combined_height))

            # タイルを結合
            for (tx, ty), img in tile_images.items():
                paste_x = (tx - start_tile_x) * TILE_SIZE
                paste_y = (ty - start_tile_y) * TILE_SIZE
                combined_img.paste(img, (paste_x, paste_y))
            
            # 結合された画像から監視領域をクロップ
            crop_x1 = global_x - start_tile_x * TILE_SIZE
            crop_y1 = global_y - start_tile_y * TILE_SIZE
            crop_x2 = crop_x1 + width
            crop_y2 = crop_y1 + height
            
            cropped_live_img = combined_img.crop((crop_x1, crop_y1, crop_x2, crop_y2))
            
            return cropped_live_img
                
        except Exception as e:
            print(f"タイル結合・クロップ中にエラーが発生しました: {e}")
            messagebox.showerror("エラー", f"画像の取得・処理中にエラーが発生しました: {e}")
            return None

    def _tick_check(self):
        ref_pixel_quad = safe_int_quad(self.realtime_ref_pixel_var.get(), DEFAULT_REF_PIXEL)

        if ref_pixel_quad == "error" or self.seal_image is None or self.monitor_size == (0, 0):
            self.status_var.set("エラー: 設定を確認してください")
            interval_ms = max(500, self.interval_sec_var.get() * 1000)
            self.after_id = self.root.after(interval_ms, self._tick_check)
            return

        # リアルタイム画像のタイルを結合して取得
        tile_x, tile_y, x_in_tile, y_in_tile = ref_pixel_quad
        cropped_live_img = self._fetch_tiles_and_crop(tile_x, tile_y, x_in_tile, y_in_tile, self.monitor_size[0], self.monitor_size[1])
        
        if cropped_live_img:
            diff_pct, diff_img = compare_images(self.seal_image, cropped_live_img)
            self.diff_pct = diff_pct
            self.current_cropped_image = cropped_live_img
            self.current_diff_image = diff_img

            self._update_images_display()
            self._update_status(diff_pct)
            self._update_graph(diff_pct)
        else:
            self.status_var.set("画像取得に失敗しました...")

        interval_ms = max(500, self.interval_sec_var.get() * 1000)
        self.after_id = self.root.after(interval_ms, self._tick_check)
        
    def _update_graph(self, diff_pct):
        """グラフ（折れ線グラフ）と円グラフを更新します。"""
        self.diff_history.append(diff_pct)
        self.time_history.append(time.time() - self.start_time)
        
        if len(self.diff_history) > self.max_history_points:
            self.diff_history.pop(0)
            self.time_history.pop(0)
        
        # 閾値の取得を安全に行う
        safe_thresholds = []
        for var, data in zip(self.threshold_vars, LEVELS_DATA):
            try:
                limit = float(var.get())
            except (tk.TclError, ValueError):
                limit = 0.0
            safe_thresholds.append((limit, data))

        sorted_thresholds = sorted(safe_thresholds, key=lambda x: x[0], reverse=True)
        
        self.line_ax.clear()
        self.line_ax.set_facecolor(self.CARD_BG)
        level_info = None
        graph_color = NORMAL_GRAPH_COLOR
        for limit, data in sorted_thresholds:
            if diff_pct >= limit:
                level_info = data
                graph_color = data["graph_color"]
                break
        self.line_ax.plot(self.time_history, self.diff_history, color=graph_color, lw=2)
        for limit, data in sorted_thresholds:
            self.line_ax.axhline(y=limit, color=data["color"], linestyle='--', lw=1, zorder=0)
            self.line_ax.text(self.time_history[-1] if self.time_history else 0, limit, f' {data["label"]}',
                         ha='right', va='bottom', color=self.FG_COLOR, fontsize=8, backgroundcolor=self.CARD_BG)
        self.line_ax.set_ylim(0, 100)
        self.line_ax.set_xlabel("時間 (秒)", color=self.FG_COLOR)
        self.line_ax.set_ylabel("差分 (%)", color=self.FG_COLOR)
        self.line_ax.tick_params(colors=self.FG_COLOR)
        self.line_ax.grid(color=self.BORDER_COLOR, linestyle=':', linewidth=0.5)
        self.line_ax.set_title("差分パーセンテージの推移", color=self.FG_COLOR, fontsize=12)
        self.line_canvas.draw_idle()

        self.pie_ax.clear()
        self.pie_ax.set_facecolor(self.CARD_BG)
        labels = ['差分', '残り']
        sizes = [diff_pct, max(0, 100 - diff_pct)]
        colors = [graph_color, '#333333']
        self.pie_ax.pie(sizes, labels=labels, colors=colors, startangle=90, counterclock=False, 
                        wedgeprops=dict(width=0.4), pctdistance=0.85, textprops={'color': 'white'})
        self.pie_ax.text(0, 0, f'{diff_pct:.2f}%', ha='center', va='center', color=self.FG_COLOR, 
                         fontweight='bold', fontsize=18, backgroundcolor=self.CARD_BG)
        self.pie_ax.set_title("現在の差分", color=self.FG_COLOR, fontsize=12, pad=10)
        self.pie_ax.axis('equal')
        self.pie_canvas.draw_idle()

    def _update_status(self, diff_pct):
        # 閾値の取得を安全に行う
        safe_thresholds = []
        for var, data in zip(self.threshold_vars, LEVELS_DATA):
            try:
                limit = float(var.get())
            except (tk.TclError, ValueError):
                limit = 0.0
            safe_thresholds.append((limit, data))

        sorted_thresholds = sorted(safe_thresholds, key=lambda x: x[0], reverse=True)
        
        level_info = None
        for limit, data in sorted_thresholds:
            if diff_pct >= limit:
                level_info = data
                break
        
        if level_info:
            self.status_label.configure(foreground=level_info["color"])
            self.status_var.set(f"{level_info['label']} ({diff_pct:.2f}%)")
        else:
            self.status_label.configure(foreground=NORMAL_COLOR)
            self.status_var.set(f"監視中... (差分: {diff_pct:.2f}%)")

    def _on_resize(self, event):
        # ウィンドウサイズが変更されたら、画像を再描画する
        self.root.after_idle(self._update_images_display)

    def _update_images_display(self):
        if self.current_cropped_image is None or self.current_diff_image is None:
            return
            
        # ラベルウィジェットの現在のサイズを取得
        try:
            label_w = self.realtime_image_label.winfo_width()
            label_h = self.realtime_image_label.winfo_height()
        except tk.TclError:
            return
        
        if label_w < 1 or label_h < 1:
            return
            
        # 元の画像のサイズ
        orig_w, orig_h = self.current_cropped_image.size
        
        # 縦横比を維持しつつ、新しいサイズを計算
        ratio_w = label_w / orig_w
        ratio_h = label_h / orig_h
        ratio = min(ratio_w, ratio_h)
        
        new_w = int(orig_w * ratio)
        new_h = int(orig_h * ratio)
        
        # 新しいサイズが0以下にならないようにする
        if new_w < 1 or new_h < 1:
            return
            
        try:
            # リアルタイム画像
            # 透過部分を考慮したリアルタイム画像の表示
            alpha_mask = self.seal_image.getchannel('A')
            realtime_with_mask = Image.new("RGBA", self.current_cropped_image.size, (0, 0, 0, 0))
            realtime_with_mask.paste(self.current_cropped_image, mask=alpha_mask)
            
            resized_rt = realtime_with_mask.resize((new_w, new_h), Image.Resampling.NEAREST)
            self.realtime_tk = ImageTk.PhotoImage(resized_rt)
            self.realtime_image_label.configure(image=self.realtime_tk)
            
            # 差分画像
            resized_df = self.current_diff_image.resize((new_w, new_h), Image.Resampling.NEAREST)
            self.diff_tk = ImageTk.PhotoImage(resized_df)
            self.diff_image_label.configure(image=self.diff_tk)
        except Exception as e:
            print(f"画像表示の更新中にエラーが発生しました: {e}")


def get_image_from_url(url: str):
    try:
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        return Image.open(io.BytesIO(resp.content))
    except requests.RequestException as e:
        print(f"画像取得失敗: {e}")
        return None

def compare_images(img1, img2):
    """
    透過ピクセルを無視して画像を比較し、透過部分を黒く塗りつぶした差分画像を返します。
    img1: 参照画像 (透過情報あり, RGBA)
    img2: リアルタイム画像 (透過情報あり, RGBA)
    """
    if img1.size != img2.size:
        w = min(img1.width, img2.width)
        h = min(img1.height, img2.height)
        img1 = img1.crop((0, 0, w, h))
        img2 = img2.crop((0, 0, w, h))

    alpha_mask = img1.getchannel('A')
    
    # 監視対象（透過していない）ピクセルの数をカウント
    opaque_pixels_count = sum(1 for p in alpha_mask.getdata() if p > 0)

    if opaque_pixels_count == 0:
        return 0.0, Image.new("RGB", img1.size, (0, 0, 0))
        
    # RGBチャンネルのみで差分を計算
    diff = ImageChops.difference(img1.convert("RGB"), img2.convert("RGB"))
    
    # 差分画像と真っ黒な画像をアルファマスクで合成
    # 透過部分（アルファ値が0）は黒に、不透明部分（アルファ値 > 0）は差分画像の色になる
    diff_visual = Image.composite(diff, Image.new("RGB", diff.size, (0, 0, 0)), alpha_mask)
    
    # 差分のあるピクセル数をカウント
    nz = sum(1 for pixel in diff_visual.getdata() if pixel != (0, 0, 0))

    diff_pct = (nz / opaque_pixels_count) * 100
    
    return diff_pct, diff_visual

def safe_int_quad(text, default):
    """カンマ区切りの4つの整数をパースし、不正な場合は'error'を返します。"""
    try:
        parts = [int(v.strip()) for v in text.split(",")]
        return tuple(parts) if len(parts) == 4 else "error"
    except (ValueError, TypeError):
        return "error"

def main():
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except ImportError:
        pass
    root = tk.Tk()
    app = VandalismDetectorApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()