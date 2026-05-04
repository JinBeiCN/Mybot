import io
import os
import random
import requests
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from typing import List, Tuple, Optional
from datetime import datetime


class RankingGenerator:
    # 颜色配置
    COLORS = {
        "bg": (15, 18, 28),           # 深蓝灰背景（调暗）
        "card_bg": (25, 30, 45),       # 卡片背景
        "bar_start": (255, 255, 255),  # 渐变起点-白色
        "bar_end": (255, 182, 193),    # 渐变终点-淡粉色
        "text": (255, 255, 255),       # 白色文字
        "subtext": (140, 145, 160),    # 灰色副文字
        "rank_gold": (255, 215, 0),    # 金色（第1名）
        "rank_silver": (192, 192, 192), # 银色（第2名）
        "rank_bronze": (205, 127, 50), # 铜色（第3名）
    }

    # 排行榜配置
    ITEM_HEIGHT = 56      # 每个条目高度
    PADDING = 24          # 内边距
    HEADER_HEIGHT = 120   # 标题区域高度
    AVATAR_SIZE = 36      # 头像大小
    IMAGE_SIZE = 1200     # 正方形图片尺寸
    BAR_HEIGHT = 16       # 进度条高度
    BAR_GAP = 4           # 条目内间距

    def __init__(self, bg_dir: str = "data/backgrounds"):
        self.bg_dir = bg_dir
        self.font_cache = {}
        self._bg_cache = None
        self._executor = ThreadPoolExecutor(max_workers=8)
        self._is_shutdown = False
        self._avatar_cache = {}

    def shutdown(self):
        """关闭线程池，释放资源"""
        if not self._is_shutdown:
            self._executor.shutdown(wait=False)
            self._is_shutdown = True

    def _get_executor(self):
        """获取线程池（如已 shutdown 则报错）"""
        if self._is_shutdown:
            raise RuntimeError("RankingGenerator 已关闭，无法继续使用")
        return self._executor

    def _sync_download_avatar(self, qq号: str) -> Image.Image:
        """同步下载头像（在线程池中执行），下载后强制裁剪为正方形"""
        cache_key = qq号
        if cache_key in self._avatar_cache:
            return self._avatar_cache[cache_key]

        try:
            url = f"https://q1.qlogo.cn/headimg_dl?dst_uin={qq号}&spec=640"
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                avatar = Image.open(io.BytesIO(response.content)).convert("RGBA")
                # 强制裁剪为正方形
                avatar = self._force_square_avatar(avatar)
                self._avatar_cache[cache_key] = avatar
                return avatar
        except Exception:
            pass

        default = Image.new("RGBA", (self.AVATAR_SIZE, self.AVATAR_SIZE), (100, 100, 120, 255))
        self._avatar_cache[cache_key] = default
        return default

    def get_font(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        """获取字体（带缓存）"""
        key = (size, bold)
        if key not in self.font_cache:
            try:
                self.font_cache[key] = ImageFont.truetype("msyh.ttc", size)
            except Exception:
                self.font_cache[key] = ImageFont.load_default()
        return self.font_cache[key]

    def round_corners(self, img: Image.Image, radius: int) -> Image.Image:
        """将图片变成圆角（确保输出尺寸不变）"""
        # 确保图片尺寸正确
        img = img.resize((self.AVATAR_SIZE, self.AVATAR_SIZE), Image.Resampling.LANCZOS)

        mask = Image.new("L", (self.AVATAR_SIZE, self.AVATAR_SIZE), 0)
        draw = ImageDraw.Draw(mask)
        draw.rounded_rectangle([(0, 0), (self.AVATAR_SIZE, self.AVATAR_SIZE)], radius=radius, fill=255)

        output = Image.new("RGBA", (self.AVATAR_SIZE, self.AVATAR_SIZE), (0, 0, 0, 0))
        output.paste(img, (0, 0), mask)
        return output

    def create_gradient_bar(self, width: int, height: int) -> Image.Image:
        """创建渐变柱状条"""
        bar = Image.new("RGB", (width, height))
        draw = ImageDraw.Draw(bar)
        for x in range(width):
            ratio = x / width if width > 0 else 0
            r = int(self.COLORS["bar_start"][0] + (self.COLORS["bar_end"][0] - self.COLORS["bar_start"][0]) * ratio)
            g = int(self.COLORS["bar_start"][1] + (self.COLORS["bar_end"][1] - self.COLORS["bar_start"][1]) * ratio)
            b = int(self.COLORS["bar_start"][2] + (self.COLORS["bar_end"][2] - self.COLORS["bar_start"][2]) * ratio)
            draw.line([(x, 0), (x, height)], fill=(r, g, b))
        return bar

    def create_rounded_bar(self, width: int, height: int, alpha: int = 64) -> Image.Image:
        """创建半透明渐变进度条"""
        bar = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(bar)

        # 淡紫色
        r, g, b = 200, 180, 255

        # 从左到右透明度从0%渐变到60%
        max_alpha = int(255 * 0.6)  # 60%
        for x in range(width):
            # 左边x=0透明度0，右边x=width-1透明度60%
            ratio = x / max(width - 1, 1)
            current_alpha = int(max_alpha * ratio)
            draw.rectangle([(x, 0), (x, height - 1)], fill=(r, g, b, current_alpha))

        return bar

    def get_random_background(self, size: Tuple[int, int]) -> Image.Image:
        """随机获取一张背景图，裁切到目标尺寸、高斯模糊20%并调暗30%"""
        # 获取所有支持的图片文件
        supported_ext = {'.jpg', '.jpeg', '.png', '.bmp', '.webp', '.gif'}
        images = []

        if os.path.exists(self.bg_dir):
            for f in os.listdir(self.bg_dir):
                ext = os.path.splitext(f.lower())[1]
                if ext in supported_ext:
                    images.append(os.path.join(self.bg_dir, f))

        if not images:
            return Image.new("RGB", size, self.COLORS["bg"])

        # 随机选择一张
        chosen = random.choice(images)

        try:
            bg = Image.open(chosen).convert("RGB")

            # 裁切到目标尺寸（居中裁切，保留主要内容）
            bg_width, bg_height = bg.size
            target_width, target_height = size

            # 计算裁切区域
            bg_ratio = bg_width / bg_height
            target_ratio = target_width / target_height

            if bg_ratio > target_ratio:
                # 图片太宽，以高度为基准裁切宽度
                new_width = int(bg_height * target_ratio)
                left = (bg_width - new_width) // 2
                top = 0
                right = left + new_width
                bottom = bg_height
            else:
                # 图片太高，以宽度为基准裁切高度
                new_height = int(bg_width / target_ratio)
                top = (bg_height - new_height) // 2
                left = 0
                right = bg_width
                bottom = top + new_height

            bg = bg.crop((left, top, right, bottom))
            bg = bg.resize(size, Image.Resampling.LANCZOS)

            # 高斯模糊20%（radius=5效果接近20%模糊程度）
            bg = bg.filter(ImageFilter.GaussianBlur(radius=5))

            # 调暗30%：在原图上叠加黑色半透明层
            dark_overlay = Image.new("RGB", size, (0, 0, 0))
            bg = Image.blend(bg, dark_overlay, 0.30)

            return bg

        except Exception:
            # 失败时返回调暗的纯色背景
            fallback = Image.new("RGB", size, self.COLORS["bg"])
            dark_overlay = Image.new("RGB", size, (0, 0, 0))
            return Image.blend(fallback, dark_overlay, 0.30)

    def _force_square_avatar(self, avatar: Image.Image) -> Image.Image:
        """强制将头像裁剪为正方形（从中心裁切）并缩放到目标尺寸"""
        width, height = avatar.size

        # 裁剪为正方形（从中心裁切）
        if width != height:
            size = min(width, height)
            left = (width - size) // 2
            top = (height - size) // 2
            avatar = avatar.crop((left, top, left + size, top + size))

        # 缩放到目标尺寸
        avatar = avatar.resize((self.AVATAR_SIZE, self.AVATAR_SIZE), Image.Resampling.LANCZOS)
        return avatar

    def _get_contrast_color(self, bg_brightness: float) -> tuple:
        """根据背景亮度智能选择对比度强的文字颜色"""
        # 背景亮度低于128用白色，高于128用黑色
        if bg_brightness < 128:
            return (255, 255, 255, 255)  # 白色
        else:
            return (0, 0, 0, 255)  # 黑色

    def generate_ranking_image(
        self,
        stats: List[Tuple[str, int, str]],
        group_id: str = ""
    ) -> bytes:
        if not stats:
            stats = [("10001", 0, "测试用户")]

        stats = stats[:15]

        # 动态高度
        ITEM_H = 74
        HEADER_H = 160
        FOOTER_H = 60
        PAD = 40
        total_h = HEADER_H + len(stats) * ITEM_H + FOOTER_H
        W = self.IMAGE_SIZE
        H = max(self.IMAGE_SIZE, total_h)

        # === 背景：深色科技风 ===
        img = Image.new("RGBA", (W, H), (8, 12, 24, 255))
        draw = ImageDraw.Draw(img)

        # 顶部渐变光晕
        for y in range(300):
            alpha = int(20 * (1 - y / 300))
            draw.line([(0, y), (W, y)], fill=(0, 180, 255, alpha))

        # 网格点阵
        for gx in range(40, W, 40):
            for gy in range(40, H, 40):
                draw.ellipse([(gx - 1, gy - 1), (gx + 2, gy + 2)], fill=(255, 255, 255, 6))

        # === 颜色 ===
        ACCENT = (0, 212, 255)      # 青
        ACCENT2 = (168, 139, 250)   # 紫
        GOLD = (255, 200, 60)
        SILVER = (180, 195, 215)
        BRONZE = (220, 160, 100)
        TEXT = (230, 235, 245)
        SUBTEXT = (110, 120, 150)
        CARD_BG = (18, 24, 42, 180)

        try:
            title_font = self.get_font(52, bold=True)
            sub_font = self.get_font(24)
        except Exception:
            title_font = self.get_font(40)
            sub_font = self.get_font(20)

        # === 顶部装饰线 ===
        for x in range(0, W, 2):
            r = int(ACCENT[0] + (ACCENT2[0] - ACCENT[0]) * x / W)
            g = int(ACCENT[1] + (ACCENT2[1] - ACCENT[1]) * x / W)
            b = int(ACCENT[2] + (ACCENT2[2] - ACCENT[2]) * x / W)
            draw.line([(x, 0), (x, 4)], fill=(r, g, b, 200))

        # === 标题 ===
        today = datetime.now().strftime("%Y.%m.%d")
        title = "TODAY RANK"
        title_bbox = draw.textbbox((0, 0), title, font=title_font)
        tx = (W - (title_bbox[2] - title_bbox[0])) // 2
        draw.text((tx, 30), title, font=title_font, fill=(255, 255, 255, 240))

        sub_bbox = draw.textbbox((0, 0), today, font=sub_font)
        draw.text(((W - (sub_bbox[2] - sub_bbox[0])) // 2, 95), today, font=sub_font, fill=ACCENT)

        # 分隔线（带渐变）
        sep_y = 140
        for sx in range(60, W - 60):
            ratio = (sx - 60) / (W - 120)
            alpha = int(80 * (1 - 2 * abs(ratio - 0.5)))
            r = int(ACCENT[0] * (1 - ratio) + ACCENT2[0] * ratio)
            g = int(ACCENT[1] * (1 - ratio) + ACCENT2[1] * ratio)
            b = int(ACCENT[2] * (1 - ratio) + ACCENT2[2] * ratio)
            draw.line([(sx, sep_y), (sx, sep_y + 1)], fill=(r, g, b, max(0, alpha)))

        # === 数据 ===
        max_count = max(count for _, count, _ in stats) if stats else 1
        total_count = sum(count for _, count, _ in stats)

        executor = self._get_executor()
        avatar_images = list(executor.map(
            lambda qq: self._sync_download_avatar(qq),
            [qq for qq, _, _ in stats]
        ))

        left_margin = 50
        rank_w = 55
        avatar_w = 60
        name_w = 170
        pct_w = 80
        right_margin = 50
        bar_area_w = W - left_margin - rank_w - avatar_w - name_w - pct_w - right_margin

        for i, (qq号, count, 昵称) in enumerate(stats):
            y = HEADER_H + i * ITEM_H
            cx = left_margin

            # 卡片背景
            card_alpha = 100 if i < 3 else 40
            draw.rounded_rectangle(
                [(cx - 12, y + 4), (W - right_margin + 12, y + ITEM_H - 4)],
                radius=12, fill=CARD_BG
            )

            # 排名徽章
            rank_colors = {0: GOLD, 1: SILVER, 2: BRONZE}
            rc = rank_colors.get(i, SUBTEXT)
            # 圆形背景
            rcx, rcy = cx + 24, y + ITEM_H // 2
            rr = 22 if i < 3 else 16
            if i < 3:
                draw.ellipse([(rcx - rr, rcy - rr), (rcx + rr, rcy + rr)], fill=rc + (40,))
                draw.ellipse([(rcx - rr, rcy - rr), (rcx + rr, rcy + rr)], outline=rc + (180,), width=2)
            try:
                rank_font = self.get_font(22 if i < 3 else 18, bold=i < 3)
            except Exception:
                rank_font = self.get_font(16)
            rank_str = f"{'#' if i < 3 else ''}{i + 1}"
            rb = draw.textbbox((0, 0), rank_str, font=rank_font)
            draw.text((rcx - (rb[2] - rb[0]) // 2, rcy - (rb[3] - rb[1]) // 2 - 2),
                      rank_str, font=rank_font, fill=rc if i < 3 else SUBTEXT)
            cx += rank_w

            # 头像
            avatar = self.round_corners(avatar_images[i], 24)
            av_size = 40 if i < 3 else 32
            avatar = avatar.resize((av_size, av_size), Image.Resampling.LANCZOS)
            ay = y + (ITEM_H - av_size) // 2
            img.paste(avatar, (cx, ay), avatar)
            cx += avatar_w

            # 昵称
            try:
                name_font = self.get_font(22 if i < 3 else 18)
            except Exception:
                name_font = self.get_font(16)
            name_text = (昵称 if 昵称 else qq号)[:8]
            nb = draw.textbbox((0, 0), name_text, font=name_font)
            draw.text((cx, y + (ITEM_H - (nb[3] - nb[1])) // 2), name_text, font=name_font, fill=TEXT)
            cx += name_w

            # 进度条
            bar_w = int((count / max_count) * bar_area_w) if max_count > 0 else 0
            bar_h = 20 if i < 3 else 14
            bar_y = y + (ITEM_H - bar_h) // 2
            # 背景槽
            draw.rounded_rectangle(
                [(cx, bar_y), (cx + bar_area_w, bar_y + bar_h)],
                radius=bar_h // 2, fill=(255, 255, 255, 10)
            )
            if bar_w > 0:
                # 渐变填充
                bar_img = Image.new("RGBA", (bar_area_w, bar_h), (0, 0, 0, 0))
                bar_draw = ImageDraw.Draw(bar_img)
                for bx in range(bar_w):
                    ratio = bx / bar_area_w
                    r = int(ACCENT[0] * (1 - ratio) + ACCENT2[0] * ratio)
                    g = int(ACCENT[1] * (1 - ratio) + ACCENT2[1] * ratio)
                    b = int(ACCENT[2] * (1 - ratio) + ACCENT2[2] * ratio)
                    bar_draw.line([(bx, 0), (bx, bar_h)], fill=(r, g, b, 220))
                # 圆角裁剪
                mask = Image.new("L", (bar_area_w, bar_h), 0)
                mdraw = ImageDraw.Draw(mask)
                mdraw.rounded_rectangle([(0, 0), (bar_area_w, bar_h)], radius=bar_h // 2, fill=255)
                img.paste(bar_img, (cx, bar_y), mask)

            # 条数
            count_text = f"{count}"
            try:
                count_font = self.get_font(20 if i < 3 else 16, bold=True)
            except Exception:
                count_font = self.get_font(14)
            cb = draw.textbbox((0, 0), count_text, font=count_font)
            count_y = y + (ITEM_H - (cb[3] - cb[1])) // 2
            # 居中于进度条
            count_x = cx + bar_area_w // 2 - (cb[2] - cb[0]) // 2
            draw.text((count_x, count_y), count_text, font=count_font, fill=(255, 255, 255, 230))

            # 百分比
            pct = f"{(count / total_count * 100):.1f}%" if total_count > 0 else "0%"
            try:
                pct_font = self.get_font(18 if i < 3 else 14)
            except Exception:
                pct_font = self.get_font(12)
            pb = draw.textbbox((0, 0), pct, font=pct_font)
            draw.text((cx + bar_area_w + 10, y + (ITEM_H - (pb[3] - pb[1])) // 2),
                      pct, font=pct_font, fill=rc if i < 3 else SUBTEXT)

        # === 底部 ===
        footer_y = HEADER_H + len(stats) * ITEM_H + 10
        footer = f"TOTAL  {total_count}  MESSAGES  ·  {len(stats)}  SPEAKERS"
        try:
            footer_font = self.get_font(18)
        except Exception:
            footer_font = self.get_font(14)
        fb = draw.textbbox((0, 0), footer, font=footer_font)
        draw.text(((W - (fb[2] - fb[0])) // 2, footer_y), footer, font=footer_font, fill=SUBTEXT)

        output = io.BytesIO()
        img.save(output, format="PNG")
        return output.getvalue()

    def generate_fortune_image(
        self,
        user_id: str,
        date: str,
        overall: int, love: int, career: int, wealth: int,
        lucky_num: int, fortune_desc: str, is_lucky: bool = False,
        nickname: str = "",
    ) -> bytes:
        """生成今日运势卡片 — 典雅金塔罗风格"""
        W, H = 800, 960
        BG = (12, 10, 8, 255)          # 近黑深棕
        img = Image.new("RGBA", (W, H), BG)
        draw = ImageDraw.Draw(img)

        GOLD = (212, 175, 55)           # 典雅金
        GOLD_DIM = (180, 150, 50)       # 暗金
        GOLD_PALE = (232, 210, 130)     # 浅金
        TEXT = (220, 215, 200)          # 暖白
        SUBTEXT = (140, 130, 115)       # 灰金
        DARK = (30, 25, 18, 180)        # 半透暗色

        # === 装饰边框 ===
        margin = 24
        border_w = 2
        # 外边框
        draw.rounded_rectangle([(margin, margin), (W - margin, H - margin)], radius=20,
                               outline=GOLD_DIM + (100,), width=border_w)
        # 内边框
        inner = 36
        draw.rounded_rectangle([(inner, inner), (W - inner, H - inner)], radius=14,
                               outline=GOLD_DIM + (60,), width=1)

        # 四角装饰菱形
        for cx, cy in [(margin + 10, margin + 10), (W - margin - 10, margin + 10),
                       (margin + 10, H - margin - 10), (W - margin - 10, H - margin - 10)]:
            draw.ellipse([(cx - 4, cy - 4), (cx + 5, cy + 5)], fill=GOLD + (120,))
            draw.ellipse([(cx - 10, cy - 10), (cx + 11, cy + 11)], outline=GOLD_DIM + (50,), width=1)

        # === 顶部光晕 ===
        for y in range(180):
            alpha = int(12 * (1 - y / 180))
            draw.line([(0, y), (W, y)], fill=GOLD + (alpha,))

        # 散落星点
        import random as _rng
        _rng.seed(hash(user_id + date) % 2**32)
        for _ in range(30):
            sx, sy = _rng.randint(50, W - 50), _rng.randint(60, 400)
            size = _rng.randint(1, 3)
            alpha = _rng.randint(40, 120)
            draw.ellipse([(sx - size, sy - size), (sx + size + 1, sy + size + 1)],
                         fill=GOLD + (alpha,))

        # === 用户头像 ===
        avatar_size = 72
        try:
            avatar = self._sync_download_avatar(user_id)
            avatar = self.round_corners(avatar, avatar_size // 2)
            avatar = avatar.resize((avatar_size, avatar_size), Image.Resampling.LANCZOS)
            ax = W // 2 - avatar_size // 2
            ay = 60
            # 光晕环
            draw.ellipse([(ax - 8, ay - 8), (ax + avatar_size + 8, ay + avatar_size + 8)],
                         outline=GOLD + (80,), width=1)
            img.paste(avatar, (ax, ay), avatar)
        except Exception:
            pass

        # === 标题 ===
        try:
            title_font = self.get_font(34, bold=True)
        except Exception:
            title_font = self.get_font(26)
        title = "今 日 运 势"
        tb = draw.textbbox((0, 0), title, font=title_font)
        draw.text(((W - (tb[2] - tb[0])) // 2, ay + avatar_size + 16), title, font=title_font, fill=GOLD)

        # 日期线
        try:
            date_font = self.get_font(16)
        except Exception:
            date_font = self.get_font(13)
        date_str = f"  {date}  "
        db = draw.textbbox((0, 0), date_str, font=date_font)
        dx = (W - (db[2] - db[0])) // 2
        dy = ay + avatar_size + 60
        # 两侧装饰横线
        line_len = 60
        draw.line([(dx - line_len, dy + db[3] // 2), (dx - 10, dy + db[3] // 2)],
                  fill=GOLD_DIM + (100,), width=1)
        draw.line([(dx + db[2] + 10, dy + db[3] // 2), (dx + db[2] + line_len, dy + db[3] // 2)],
                  fill=GOLD_DIM + (100,), width=1)
        draw.text((dx, dy), date_str, font=date_font, fill=SUBTEXT)

        # === 四项运势 ===
        categories = [
            ("总 合 运 势", overall),
            ("爱 情 运 势", love),
            ("事 业 运 势", career),
            ("财 运 指 数", wealth),
        ]

        bar_start_y = ay + avatar_size + 100
        bar_area_w = W - 160

        for ci, (cat_name, score) in enumerate(categories):
            cy = bar_start_y + ci * 110

            # 名称
            try:
                cat_font = self.get_font(18)
            except Exception:
                cat_font = self.get_font(15)
            draw.text((80, cy + 6), cat_name, font=cat_font, fill=SUBTEXT)

            # 分数
            try:
                score_font = self.get_font(26, bold=True)
            except Exception:
                score_font = self.get_font(20)
            score_str = f"{score}/10"
            sb2 = draw.textbbox((0, 0), score_str, font=score_font)
            draw.text((W - 80 - (sb2[2] - sb2[0]), cy), score_str, font=score_font, fill=GOLD)

            # 进度条
            bar_h = 10
            bar_x, bar_y = 80, cy + 36
            # 暗底
            draw.rounded_rectangle(
                [(bar_x, bar_y), (bar_x + bar_area_w, bar_y + bar_h)],
                radius=bar_h // 2, fill=(255, 255, 255, 6)
            )
            fill_w = int(bar_area_w * score / 10)
            if fill_w > 0:
                bar_img = Image.new("RGBA", (bar_area_w, bar_h), (0, 0, 0, 0))
                bar_draw = ImageDraw.Draw(bar_img)
                for bx in range(fill_w):
                    ratio = bx / bar_area_w
                    r = int(GOLD_DIM[0] + (GOLD[0] - GOLD_DIM[0]) * ratio)
                    g = int(GOLD_DIM[1] + (GOLD[1] - GOLD_DIM[1]) * ratio)
                    b = int(GOLD_DIM[2] + (GOLD[2] - GOLD_DIM[2]) * ratio)
                    bar_draw.line([(bx, 0), (bx, bar_h)], fill=(r, g, b, 220))
                mask = Image.new("L", (bar_area_w, bar_h), 0)
                mdraw = ImageDraw.Draw(mask)
                mdraw.rounded_rectangle([(0, 0), (bar_area_w, bar_h)], radius=bar_h // 2, fill=255)
                img.paste(bar_img, (bar_x, bar_y), mask)

            # 星标
            try:
                star_font = self.get_font(12)
            except Exception:
                star_font = self.get_font(10)
            stars = "★" * score + "  " + "☆" * (10 - score)
            draw.text((bar_x, bar_y + 14), stars, font=star_font, fill=GOLD + (140,))

        # === 底部区：幸运数字 + 箴言 ===
        desc_y = bar_start_y + 4 * 110 + 30
        sep_y = desc_y - 16
        # 分隔装饰
        for sx in range(80, W - 80, 4):
            alpha = int(60 * (1 - abs(sx - W // 2) / (W // 2 - 80)))
            draw.line([(sx, sep_y), (sx + 2, sep_y)], fill=GOLD + (max(0, alpha),))

        # 幸运数字
        try:
            lucky_font = self.get_font(56, bold=True)
        except Exception:
            lucky_font = self.get_font(42)
        lucky_str = str(lucky_num)
        lb = draw.textbbox((0, 0), lucky_str, font=lucky_font)
        lx = W // 2 - 100 - (lb[2] - lb[0])
        ly = desc_y + 10
        draw.text((lx, ly), lucky_str, font=lucky_font, fill=GOLD)

        try:
            lucky_label = self.get_font(14)
        except Exception:
            lucky_label = self.get_font(11)
        draw.text((lx, ly - 16), "LUCKY  NUMBER", font=lucky_label, fill=SUBTEXT)

        # 竖分隔
        sep_x = W // 2 + 20
        draw.line([(sep_x, desc_y), (sep_x, desc_y + 130)], fill=GOLD + (60,), width=1)

        # 箴言
        try:
            desc_font = self.get_font(17)
        except Exception:
            desc_font = self.get_font(14)
        desc_lines = self._wrap_text(draw, fortune_desc, W - (sep_x + 60), desc_font)
        for di, dl in enumerate(desc_lines[:4]):
            draw.text((sep_x + 20, desc_y + 10 + di * 30), dl, font=desc_font, fill=TEXT)

        # === 底部 ===
        footer = "BEIXAI  ·  D A I L Y   F O R T U N E"
        try:
            footer_font = self.get_font(11)
        except Exception:
            footer_font = self.get_font(9)
        fb = draw.textbbox((0, 0), footer, font=footer_font)
        draw.text(((W - (fb[2] - fb[0])) // 2, H - 60), footer, font=footer_font, fill=SUBTEXT + (80,))

        output = io.BytesIO()
        img.save(output, format="PNG")
        return output.getvalue()

    def _wrap_text(self, draw, text: str, max_w: int, font) -> list[str]:
        """简单换行"""
        lines = []
        current = ""
        for ch in text:
            test = current + ch
            if draw.textbbox((0, 0), test, font=font)[2] > max_w and current:
                lines.append(current)
                current = ch
            else:
                current = test
        if current:
            lines.append(current)
        return lines

    def generate_summary_image(
        self,
        title: str,
        content_lines: List[str],
        footer: str = ""
    ) -> bytes:
        """生成总结图片（优化版式，动态高度）"""
        LINE_HEIGHT = 28
        SECTION_GAP = 8
        PADDING = 30
        TITLE_SIZE = 22
        CONTENT_SIZE = 15
        FOOTER_SIZE = 12
        SECTION_HEADER_SIZE = 14

        ACCENT_COLOR = (100, 149, 237)
        SUBTEXT_COLOR = (140, 145, 160)
        IMG_WIDTH = 650

        # 先创建临时画布，用于精确计算换行后的实际高度
        measure_img = Image.new("RGB", (IMG_WIDTH, 10), self.COLORS["bg"])
        measure_draw = ImageDraw.Draw(measure_img)

        try:
            title_font = self.get_font(TITLE_SIZE, bold=True)
        except Exception:
            title_font = self.get_font(TITLE_SIZE)

        try:
            content_font = self.get_font(CONTENT_SIZE)
            section_font = self.get_font(SECTION_HEADER_SIZE, bold=True)
        except Exception:
            content_font = self.get_font(CONTENT_SIZE)
            section_font = self.get_font(SECTION_HEADER_SIZE)

        TITLE_HEIGHT = 50
        FOOTER_HEIGHT = 30 if footer else 0
        FOOTER_Y_GAP = 20 if footer else 0
        max_width = IMG_WIDTH - 2 * PADDING

        # 精确计算内容高度
        content_height = 0
        for line in content_lines:
            if not line.strip():
                content_height += SECTION_GAP
                continue

            is_section_header = (
                line.strip() and
                not line.startswith("  ") and
                not line.startswith("#") and
                ":" in line and
                len(line) < 40
            )

            if is_section_header:
                content_height += LINE_HEIGHT
            else:
                line_text = line.strip() if line.startswith("  ") else line
                content_height += self._measure_wrapped_text_height(measure_draw, line_text, max_width, content_font, LINE_HEIGHT)

        total_height = PADDING + TITLE_HEIGHT + 10 + 15 + content_height + FOOTER_Y_GAP + FOOTER_HEIGHT + PADDING

        img = Image.new("RGB", (IMG_WIDTH, total_height), self.COLORS["bg"])
        draw = ImageDraw.Draw(img)

        bg_brightness = 30
        contrast_rgb = self._get_contrast_color(bg_brightness)[:3]

        title_bbox = draw.textbbox((0, 0), title, font=title_font)
        title_width = title_bbox[2] - title_bbox[0]
        draw.text(((IMG_WIDTH - title_width) // 2, PADDING), title, font=title_font, fill=contrast_rgb)

        line_y = PADDING + TITLE_HEIGHT + 10
        draw.line([(PADDING, line_y), (IMG_WIDTH - PADDING, line_y)], fill=contrast_rgb, width=1)

        y = line_y + 15

        for line in content_lines:
            if not line.strip():
                y += SECTION_GAP
                continue

            is_section_header = (
                line.strip() and
                not line.startswith("  ") and
                not line.startswith("#") and
                ":" in line and
                len(line) < 40
            )

            if is_section_header:
                draw.text((PADDING, y), line.strip(), font=section_font, fill=ACCENT_COLOR)
                y += LINE_HEIGHT
            else:
                line_text = line.strip() if line.startswith("  ") else line
                used_height = self._draw_wrapped_text(draw, line_text, PADDING, y, max_width, content_font, contrast_rgb, LINE_HEIGHT)
                y += used_height

        if footer:
            try:
                footer_font = self.get_font(FOOTER_SIZE)
            except Exception:
                footer_font = self.get_font(FOOTER_SIZE)
            footer_bbox = draw.textbbox((0, 0), footer, font=footer_font)
            footer_width = footer_bbox[2] - footer_bbox[0]
            draw.text(((IMG_WIDTH - footer_width) // 2, y + 10), footer, font=footer_font, fill=SUBTEXT_COLOR)

        output = io.BytesIO()
        img.save(output, format="PNG")
        return output.getvalue()

    def _measure_wrapped_text_height(self, draw, text: str, max_width: int, font, line_height: int) -> int:
        """测量自动换行文本高度"""
        if not text:
            return 0

        lines = 1
        current_line = ""
        for char in text:
            test_line = current_line + char
            test_bbox = draw.textbbox((0, 0), test_line, font=font)
            if test_bbox[2] - test_bbox[0] > max_width:
                lines += 1
                current_line = char
            else:
                current_line = test_line
        return lines * line_height

    def _draw_wrapped_text(self, draw, text: str, x: int, y: int, max_width: int, font, fill, line_height: int = 28):
        """绘制自动换行文本，返回实际占用高度"""
        if not text:
            return 0

        current_line = ""
        lines = 0
        for char in text:
            test_line = current_line + char
            test_bbox = draw.textbbox((0, 0), test_line, font=font)
            if test_bbox[2] - test_bbox[0] > max_width:
                draw.text((x, y), current_line, font=font, fill=fill)
                y += line_height
                lines += 1
                current_line = char
            else:
                current_line = test_line

        if current_line:
            draw.text((x, y), current_line, font=font, fill=fill)
            lines += 1

        return lines * line_height


def generate_ranking_image(
    stats: List[Tuple[str, int, str]],
    group_id: str = ""
) -> bytes:
    """便捷函数：生成排行榜图片"""
    generator = RankingGenerator()
    return generator.generate_ranking_image(stats, group_id)


def generate_summary_image(
    title: str,
    content_lines: list,
    footer: str = ""
) -> bytes:
    """生成总结图片"""
    generator = RankingGenerator()
    return generator.generate_summary_image(title, content_lines, footer)
