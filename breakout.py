"""블럭깨기 (Breakout) - 표준 라이브러리 tkinter만 사용

조작법
    ← →      패들 이동
    마우스    패들 이동
    Space    공 발사 / 일시정지 해제 / 재시작
    P        일시정지
    R        재시작
    Esc      종료

아이템 (벽돌이 깨질 때 일정 확률로 떨어지며, 패들로 받으면 발동)
    W  패들 확대      일정 시간 패들이 길어진다
    M  멀티볼         공이 여러 개로 갈라진다
    S  감속           일정 시간 공이 느려진다
    P  관통           일정 시간 공이 벽돌을 뚫고 지나간다
    ♥  목숨 +1
"""

import tkinter as tk
import math
import random

# ---------------------------------------------------------------- 설정 값
WIDTH, HEIGHT = 800, 600

PADDLE_W, PADDLE_H = 110, 14
WIDE_W = 180              # 패들 확대 아이템 적용 시 폭
PADDLE_Y = HEIGHT - 45
PADDLE_SPEED = 10         # 키보드 조작 시 프레임당 이동 픽셀

BALL_R = 8
BALL_SPEED_START = 8.5    # 1레벨 시작 속도
BALL_SPEED_LEVEL_UP = 0.8 # 레벨이 오를 때마다 시작 속도에 더해지는 값
BALL_SPEED_MAX = 15.0
BALL_SPEED_UP = 0.15      # 벽돌을 깰 때마다 붙는 가속
MAX_BALLS = 8

BRICK_ROWS, BRICK_COLS = 6, 10
BRICK_TOP = 70
BRICK_H = 24
BRICK_GAP = 4
SIDE_MARGIN = 30

LIVES_START = 3
LIVES_MAX = 5
FRAME_MS = 16             # 약 60 FPS

# ---- 아이템
DROP_CHANCE = 0.25        # 벽돌이 완전히 깨질 때 아이템이 나올 확률
ITEM_W, ITEM_H = 36, 20
ITEM_SPEED = 4            # 아이템 낙하 속도(프레임당 픽셀)
EFFECT_FRAMES = 620       # 지속 효과 시간 (약 10초)
SLOW_FACTOR = 0.65
ITEM_SCORE = 50

ITEM_TYPES = {
    #  종류      표시  색           가중치  효과 이름
    "wide":   {"label": "W", "color": "#4dc3ff", "weight": 3, "name": "패들 확대"},
    "multi":  {"label": "M", "color": "#ffd93d", "weight": 3, "name": ""},
    "slow":   {"label": "S", "color": "#6bd968", "weight": 2, "name": "감속"},
    "pierce": {"label": "P", "color": "#ff9f43", "weight": 2, "name": "관통"},
    "life":   {"label": "♥", "color": "#ff5c7a", "weight": 1, "name": ""},
}
TIMED_EFFECTS = ("wide", "slow", "pierce")

BG = "#12121c"
FG = "#e8e8f0"
ACCENT = "#5ec8f8"
PIERCE_COLOR = "#ff9f43"

# 행마다 색과 점수. 위쪽 행일수록 단단하고(hp) 점수가 높다.
ROW_STYLE = [
    {"color": "#ff5c7a", "hp": 2, "score": 70},
    {"color": "#ff9f43", "hp": 2, "score": 50},
    {"color": "#ffd93d", "hp": 1, "score": 40},
    {"color": "#6bd968", "hp": 1, "score": 30},
    {"color": "#4dc3ff", "hp": 1, "score": 20},
    {"color": "#a78bfa", "hp": 1, "score": 10},
]
# hp가 1 줄었을 때 쓰는 흐린 색
CRACKED = "#7a7a92"

FONT = "맑은 고딕"


class Breakout:
    def __init__(self, root):
        self.root = root
        root.title("블럭깨기")
        root.resizable(False, False)

        self.canvas = tk.Canvas(
            root, width=WIDTH, height=HEIGHT,
            bg=BG, highlightthickness=0,
        )
        self.canvas.pack()

        # 입력 상태
        self.keys = set()
        root.bind("<KeyPress>", self._on_key_press)
        root.bind("<KeyRelease>", lambda e: self.keys.discard(e.keysym))
        self.canvas.bind("<Motion>", self._on_mouse)
        self.canvas.focus_set()

        self.state = "ready"
        self.hud = None
        self.effect_text = None
        self.overlay = []
        self.new_game()
        self._tick()

    # ------------------------------------------------------------ 게임 생성
    def new_game(self):
        self.score = 0
        self.lives = LIVES_START
        self.level = 1
        self.start_level()

    def start_level(self):
        self.canvas.delete("all")
        # delete("all")로 캔버스 항목이 전부 사라지므로 id를 들고 있는 값도 초기화한다
        self.hud = None
        self.effect_text = None
        self.overlay = []
        self.balls = []
        self.items = []
        self.timers = {k: 0 for k in TIMED_EFFECTS}
        self.paddle_w = PADDLE_W

        self.speed = min(
            BALL_SPEED_MAX,
            BALL_SPEED_START + (self.level - 1) * BALL_SPEED_LEVEL_UP,
        )
        self._build_bricks()

        # 패들
        self.px = WIDTH / 2
        self.paddle = self.canvas.create_rectangle(
            0, 0, 0, 0, fill=ACCENT, outline="",
        )
        self._draw_paddle()

        self._draw_hud()
        self._reset_ball()

    def _build_bricks(self):
        """벽돌 격자를 만들고 {canvas_id: {...}} 형태로 보관한다."""
        self.bricks = {}
        usable = WIDTH - SIDE_MARGIN * 2
        bw = (usable - BRICK_GAP * (BRICK_COLS - 1)) / BRICK_COLS

        for row in range(BRICK_ROWS):
            style = ROW_STYLE[row % len(ROW_STYLE)]
            y1 = BRICK_TOP + row * (BRICK_H + BRICK_GAP)
            for col in range(BRICK_COLS):
                x1 = SIDE_MARGIN + col * (bw + BRICK_GAP)
                rect = (x1, y1, x1 + bw, y1 + BRICK_H)
                bid = self.canvas.create_rectangle(
                    *rect, fill=style["color"], outline="",
                )
                self.bricks[bid] = {
                    "hp": style["hp"],
                    "score": style["score"],
                    "rect": rect,   # 충돌 검사마다 canvas.coords를 부르지 않도록 캐싱
                }

    def _new_ball(self, x, y, vx=0.0, vy=0.0):
        bid = self.canvas.create_oval(0, 0, 0, 0, fill=FG, outline="")
        ball = {"id": bid, "x": x, "y": y, "vx": vx, "vy": vy}
        self._draw_ball(ball)
        return ball

    def _reset_ball(self):
        """공 하나를 패들 위에 올려놓고 발사 대기 상태로 만든다."""
        self._clear_effects()
        for ball in self.balls:
            self.canvas.delete(ball["id"])
        self.balls = [self._new_ball(self.px, PADDLE_Y - BALL_R - 1)]
        self.state = "ready"
        self._show_overlay("Space 를 눌러 시작", f"LEVEL {self.level}")

    def _launch(self):
        ball = self.balls[0]
        angle = math.radians(random.uniform(-50, 50))
        speed = self._cur_speed()
        ball["vx"] = math.sin(angle) * speed
        ball["vy"] = -math.cos(angle) * speed
        self.state = "playing"
        self._clear_overlay()

    # ------------------------------------------------------------ 입력 처리
    def _on_key_press(self, event):
        key = event.keysym
        self.keys.add(key)

        if key == "Escape":
            self.root.destroy()
        elif key in ("r", "R"):
            self.new_game()
        elif key in ("p", "P"):
            if self.state == "playing":
                self.state = "paused"
                self._show_overlay("일시정지", "P 또는 Space 로 계속")
            elif self.state == "paused":
                self.state = "playing"
                self._clear_overlay()
        elif key == "space":
            if self.state == "ready":
                self._launch()
            elif self.state == "paused":
                self.state = "playing"
                self._clear_overlay()
            elif self.state == "clear":
                self.start_level()
            elif self.state == "gameover":
                self.new_game()

    def _on_mouse(self, event):
        if self.state in ("ready", "playing"):
            self.px = event.x
            self._clamp_paddle()

    # ------------------------------------------------------------ 메인 루프
    def _tick(self):
        if self.state in ("ready", "playing"):
            self._move_paddle()
        if self.state == "playing":
            self._update_effects()
            self._move_balls()
            self._move_items()
        elif self.state == "ready":
            # 발사 전에는 공이 패들을 따라다닌다
            ball = self.balls[0]
            ball["x"] = self.px
            self._draw_ball(ball)

        self.root.after(FRAME_MS, self._tick)

    def _move_paddle(self):
        if "Left" in self.keys:
            self.px -= PADDLE_SPEED
        if "Right" in self.keys:
            self.px += PADDLE_SPEED
        self._clamp_paddle()

    def _clamp_paddle(self):
        half = self.paddle_w / 2
        self.px = max(half, min(WIDTH - half, self.px))
        self._draw_paddle()

    def _cur_speed(self):
        return self.speed * (SLOW_FACTOR if self.timers["slow"] > 0 else 1.0)

    def _move_balls(self):
        target = self._cur_speed()
        for ball in list(self.balls):
            # 가속·감속 아이템 등으로 목표 속도가 바뀌면 방향은 그대로 두고 크기만 맞춘다
            mag = math.hypot(ball["vx"], ball["vy"])
            if mag and abs(mag - target) > 0.01:
                k = target / mag
                ball["vx"] *= k
                ball["vy"] *= k

            # 빠른 공이 벽돌을 통과하지 않도록 한 프레임을 잘게 나눠 이동한다
            steps = max(1, int(target // BALL_R) + 1)
            lost = False
            for _ in range(steps):
                ball["x"] += ball["vx"] / steps
                ball["y"] += ball["vy"] / steps
                if self._handle_walls(ball):
                    lost = True
                    break
                self._handle_paddle(ball)
                self._handle_bricks(ball)
                if self.state != "playing":   # 레벨 클리어
                    return

            if lost:
                self.canvas.delete(ball["id"])
                self.balls.remove(ball)
            else:
                self._draw_ball(ball)

        if not self.balls:
            self._lose_life()

    # ------------------------------------------------------------ 충돌 처리
    def _handle_walls(self, ball):
        """벽 반사를 처리한다. 공이 바닥으로 떨어졌으면 True."""
        if ball["x"] - BALL_R <= 0:
            ball["x"] = BALL_R
            ball["vx"] = abs(ball["vx"])
        elif ball["x"] + BALL_R >= WIDTH:
            ball["x"] = WIDTH - BALL_R
            ball["vx"] = -abs(ball["vx"])

        if ball["y"] - BALL_R <= 0:
            ball["y"] = BALL_R
            ball["vy"] = abs(ball["vy"])
        elif ball["y"] - BALL_R > HEIGHT:
            return True
        return False

    def _handle_paddle(self, ball):
        if ball["vy"] <= 0:
            return
        half = self.paddle_w / 2
        x1, x2 = self.px - half, self.px + half
        y1, y2 = PADDLE_Y, PADDLE_Y + PADDLE_H

        if self._overlaps(ball, x1, y1, x2, y2) is None:
            return

        # 패들 중심에서 얼마나 벗어나 맞았는지(-1 ~ 1)로 반사 각도를 정한다
        offset = (ball["x"] - self.px) / half
        offset = max(-1.0, min(1.0, offset))
        angle = math.radians(offset * 60)

        speed = self._cur_speed()
        ball["vx"] = math.sin(angle) * speed
        ball["vy"] = -math.cos(angle) * speed
        ball["y"] = y1 - BALL_R - 0.1

    def _handle_bricks(self, ball):
        pierce = self.timers["pierce"] > 0
        for bid in list(self.bricks):
            axis = self._overlaps(ball, *self.bricks[bid]["rect"])
            if axis is None:
                continue

            if pierce:
                # 관통: 반사하지 않고 벽돌을 한 번에 부수며 계속 나아간다
                self._hit_brick(bid, destroy=True)
                if self.state != "playing":
                    return
                continue

            if axis == "x":
                ball["vx"] = -ball["vx"]
                ball["x"] += ball["vx"] * 0.5
            else:
                ball["vy"] = -ball["vy"]
                ball["y"] += ball["vy"] * 0.5

            self._hit_brick(bid)
            break   # 한 프레임에 한 번만 반사시킨다

    def _overlaps(self, ball, x1, y1, x2, y2):
        """공과 사각형이 겹치면 반사할 축('x' 또는 'y')을, 아니면 None을 반환."""
        bx1, by1 = ball["x"] - BALL_R, ball["y"] - BALL_R
        bx2, by2 = ball["x"] + BALL_R, ball["y"] + BALL_R
        if bx2 <= x1 or bx1 >= x2 or by2 <= y1 or by1 >= y2:
            return None

        # 파고든 깊이가 얕은 쪽이 실제로 부딪힌 면이다
        depth_x = min(bx2 - x1, x2 - bx1)
        depth_y = min(by2 - y1, y2 - by1)
        return "x" if depth_x < depth_y else "y"

    def _hit_brick(self, bid, destroy=False):
        info = self.bricks[bid]
        info["hp"] = 0 if destroy else info["hp"] - 1

        if info["hp"] > 0:
            self.canvas.itemconfig(bid, fill=CRACKED)
            self.score += info["score"] // 4
        else:
            x1, y1, x2, y2 = info["rect"]
            self.canvas.delete(bid)
            del self.bricks[bid]
            self.score += info["score"]
            self.speed = min(BALL_SPEED_MAX, self.speed + BALL_SPEED_UP)
            self._maybe_drop((x1 + x2) / 2, (y1 + y2) / 2)

        self._draw_hud()

        if not self.bricks:
            self._level_clear()

    # ------------------------------------------------------------ 아이템
    def _maybe_drop(self, x, y):
        if random.random() >= DROP_CHANCE:
            return
        kinds = list(ITEM_TYPES)
        kind = random.choices(kinds, weights=[ITEM_TYPES[k]["weight"] for k in kinds])[0]
        spec = ITEM_TYPES[kind]

        rect = self.canvas.create_rectangle(
            x - ITEM_W / 2, y - ITEM_H / 2, x + ITEM_W / 2, y + ITEM_H / 2,
            fill=spec["color"], outline=FG, width=2,
        )
        text = self.canvas.create_text(
            x, y, text=spec["label"], fill="#12121c", font=(FONT, 11, "bold"),
        )
        self.items.append({"rect": rect, "text": text, "kind": kind, "x": x, "y": y})

    def _move_items(self):
        half = self.paddle_w / 2
        for item in list(self.items):
            item["y"] += ITEM_SPEED
            self.canvas.move(item["rect"], 0, ITEM_SPEED)
            self.canvas.move(item["text"], 0, ITEM_SPEED)

            top = item["y"] - ITEM_H / 2
            bottom = item["y"] + ITEM_H / 2
            caught = (
                bottom >= PADDLE_Y
                and top <= PADDLE_Y + PADDLE_H
                and abs(item["x"] - self.px) <= half + ITEM_W / 2
            )
            if caught:
                self._remove_item(item)
                self._apply_item(item["kind"])
            elif top > HEIGHT:
                self._remove_item(item)

    def _remove_item(self, item):
        self.canvas.delete(item["rect"])
        self.canvas.delete(item["text"])
        self.items.remove(item)

    def _apply_item(self, kind):
        self.score += ITEM_SCORE
        if kind in TIMED_EFFECTS:
            self.timers[kind] = EFFECT_FRAMES
        elif kind == "multi":
            self._split_balls()
        elif kind == "life":
            self.lives = min(LIVES_MAX, self.lives + 1)
        self._draw_hud()

    def _split_balls(self):
        """현재 공마다 좌우로 벌어지는 공을 두 개씩 더 만든다."""
        for ball in list(self.balls):
            for deg in (-25, 25):
                if len(self.balls) >= MAX_BALLS:
                    return
                a = math.radians(deg)
                c, s = math.cos(a), math.sin(a)
                vx = ball["vx"] * c - ball["vy"] * s
                vy = ball["vx"] * s + ball["vy"] * c
                self.balls.append(self._new_ball(ball["x"], ball["y"], vx, vy))

    def _update_effects(self):
        for kind in TIMED_EFFECTS:
            if self.timers[kind] > 0:
                self.timers[kind] -= 1

        self.paddle_w = WIDE_W if self.timers["wide"] > 0 else PADDLE_W
        self._clamp_paddle()

        fill = PIERCE_COLOR if self.timers["pierce"] > 0 else FG
        for ball in self.balls:
            self.canvas.itemconfig(ball["id"], fill=fill)
        self._draw_effect_text()

    def _clear_effects(self):
        """떨어지는 아이템을 지우고 지속 효과를 모두 해제한다."""
        for item in list(self.items):
            self._remove_item(item)
        self.timers = {k: 0 for k in TIMED_EFFECTS}
        self.paddle_w = PADDLE_W
        self._clamp_paddle()
        self._draw_effect_text()

    # ------------------------------------------------------------ 상태 전이
    def _lose_life(self):
        self.lives -= 1
        self._draw_hud()
        if self.lives <= 0:
            self._clear_effects()
            self.state = "gameover"
            self._show_overlay("GAME OVER", f"점수 {self.score}   ·   Space / R 키로 재시작")
        else:
            self._reset_ball()

    def _level_clear(self):
        self.level += 1
        self.score += 200
        self.state = "clear"
        self._show_overlay(
            f"LEVEL {self.level - 1} CLEAR!",
            "잠시 후 다음 레벨  ·  Space 로 바로 시작",
        )
        self.root.after(1500, self._next_level)

    def _next_level(self):
        if self.state == "clear":
            self.start_level()

    # ------------------------------------------------------------ 그리기
    def _draw_paddle(self):
        half = self.paddle_w / 2
        self.canvas.coords(
            self.paddle,
            self.px - half, PADDLE_Y, self.px + half, PADDLE_Y + PADDLE_H,
        )

    def _draw_ball(self, ball):
        self.canvas.coords(
            ball["id"],
            ball["x"] - BALL_R, ball["y"] - BALL_R,
            ball["x"] + BALL_R, ball["y"] + BALL_R,
        )

    def _draw_hud(self):
        text = f"SCORE {self.score:>5}      LEVEL {self.level}      LIFE {'♥' * self.lives}"
        if self.hud is None:
            self.hud = self.canvas.create_text(
                WIDTH / 2, 30, text=text, fill=FG, font=(FONT, 14, "bold"),
            )
        else:
            self.canvas.itemconfig(self.hud, text=text)

    def _draw_effect_text(self):
        """화면 왼쪽 아래에 발동 중인 지속 효과와 남은 시간을 표시한다."""
        parts = []
        for kind in TIMED_EFFECTS:
            frames = self.timers[kind]
            if frames > 0:
                secs = math.ceil(frames * FRAME_MS / 1000)
                parts.append(f"{ITEM_TYPES[kind]['name']} {secs}s")
        text = "   ·   ".join(parts)

        if self.effect_text is None:
            self.effect_text = self.canvas.create_text(
                14, HEIGHT - 16, text=text, anchor="w",
                fill=ACCENT, font=(FONT, 11, "bold"),
            )
        else:
            self.canvas.itemconfig(self.effect_text, text=text)

    def _show_overlay(self, title, subtitle=""):
        self._clear_overlay()
        self.overlay.append(self.canvas.create_rectangle(
            0, HEIGHT / 2 - 70, WIDTH, HEIGHT / 2 + 70,
            fill="#000000", outline="", stipple="gray50",
        ))
        self.overlay.append(self.canvas.create_text(
            WIDTH / 2, HEIGHT / 2 - 14, text=title,
            fill=ACCENT, font=(FONT, 30, "bold"),
        ))
        if subtitle:
            self.overlay.append(self.canvas.create_text(
                WIDTH / 2, HEIGHT / 2 + 28, text=subtitle,
                fill=FG, font=(FONT, 14),
            ))

    def _clear_overlay(self):
        for item in self.overlay:
            self.canvas.delete(item)
        self.overlay = []


def main():
    root = tk.Tk()
    Breakout(root)
    root.mainloop()


if __name__ == "__main__":
    main()
