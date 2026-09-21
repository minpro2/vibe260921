"""뱀 게임 (Snake) 사람 vs AI - 표준 라이브러리 tkinter만 사용

사람이 조종하는 초록 뱀과 AI가 조종하는 파란 뱀이 한 판에서 사과를 놓고 경쟁한다.

조작법
    방향키 / WASD   내 뱀(초록) 이동 방향
    Space           시작 / 게임 오버 후 재시작
    P               일시정지
    R               재시작
    Esc             종료

규칙
    사과를 먼저 TARGET 개 먹는 쪽이 이긴다.
    벽, 자기 몸, 상대 몸에 부딪히면 죽고 상대가 이긴다. 머리끼리 부딪히면 둘 다 죽는다.
    (둘 다 죽으면 사과를 더 많이 먹은 쪽이 이기고, 같으면 무승부)
    사과를 먹으면 몸이 길어지고, 두 뱀이 먹은 사과가 늘수록 판이 조금씩 빨라진다.

AI
    - 상대보다 먼저 닿을 수 있는 사과를 우선 노리고, 없으면 가장 가까운 사과로 간다 (BFS 최단 경로).
    - 이동한 뒤 갇힐 만큼 좁은 곳(자기 몸 길이보다 작은 공간)은 피한다.
    - 상대 머리가 바로 다음에 들어올 수 있는 칸은 다른 길이 있으면 피한다.
"""

import random
import tkinter as tk
from collections import deque

# ---------------------------------------------------------------- 설정 값
CELL = 24                 # 한 칸의 픽셀 크기
COLS, ROWS = 26, 20
HUD_H = 50                # 위쪽 점수 표시줄 높이
WIDTH, HEIGHT = COLS * CELL, ROWS * CELL + HUD_H

START_LEN = 3
TARGET = 10               # 먼저 먹으면 이기는 사과 개수
FOOD_COUNT = 3            # 판 위에 동시에 놓이는 사과 개수
DELAY_START = 140         # 시작 이동 간격(ms)
DELAY_MIN = 60
DELAY_STEP = 3            # 사과가 한 개 먹힐 때마다 줄어드는 간격

BG = "#12121c"
GRID = "#1a1a28"
FG = "#e8e8f0"
ACCENT = "#5ec8f8"
HIGHLIGHT = "#ffd93d"
FOOD_COLOR = "#ff5c7a"
HUMAN_COLORS = ("#8dff7a", "#4fbf5a")      # (머리, 몸)
AI_COLORS = ("#6fd0ff", "#3a8fd6")
DEAD_COLORS = ("#7a7a92", "#55556a")

FONT = "맑은 고딕"

DIR_LIST = [(0, -1), (0, 1), (-1, 0), (1, 0)]
DIRECTIONS = {
    "Up": (0, -1), "w": (0, -1), "W": (0, -1),
    "Down": (0, 1), "s": (0, 1), "S": (0, 1),
    "Left": (-1, 0), "a": (-1, 0), "A": (-1, 0),
    "Right": (1, 0), "d": (1, 0), "D": (1, 0),
}


class Player:
    """뱀 한 마리의 상태. body[0] 이 머리다."""

    def __init__(self, name, body, direction, colors):
        self.name = name
        self.body = body
        self.direction = direction
        self.queue = []           # 아직 반영되지 않은 방향 입력 (사람 전용)
        self.apples = 0
        self.alive = True
        self.colors = colors


class Snake:
    def __init__(self, root):
        self.root = root
        root.title("뱀 게임 - 사람 vs AI")
        root.resizable(False, False)

        self.canvas = tk.Canvas(root, width=WIDTH, height=HEIGHT, bg=BG, highlightthickness=0)
        self.canvas.pack()
        root.bind("<KeyPress>", self._on_key)
        self.canvas.focus_set()

        self.wins = {"YOU": 0, "AI": 0}     # 이번 실행 동안의 누적 승수
        self.new_game()
        self._tick()

    # ------------------------------------------------------------ 게임 생성
    def new_game(self):
        # 서로 다른 줄에서 마주 보고 출발한다 (시작하자마자 정면충돌하지 않도록)
        self.human = Player(
            "YOU", [(6 - i, 6) for i in range(START_LEN)], (1, 0), HUMAN_COLORS)
        self.ai = Player(
            "AI", [(COLS - 7 + i, ROWS - 7) for i in range(START_LEN)], (-1, 0), AI_COLORS)
        self.foods = []
        self._refill_foods()
        self.delay = DELAY_START
        self.state = "ready"
        self.winner = None
        self.reason = ""
        self._draw()

    def _refill_foods(self):
        taken = set(self.human.body) | set(self.ai.body) | set(self.foods)
        free = [(x, y) for x in range(COLS) for y in range(ROWS) if (x, y) not in taken]
        while len(self.foods) < FOOD_COUNT and free:
            self.foods.append(free.pop(random.randrange(len(free))))

    # ------------------------------------------------------------ 입력 처리
    def _on_key(self, event):
        key = event.keysym
        if key == "Escape":
            self.root.destroy()
        elif key in ("r", "R"):
            self.new_game()
        elif key in ("p", "P"):
            if self.state == "playing":
                self.state = "paused"
                self._draw()
            elif self.state == "paused":
                self.state = "playing"
                self._draw()
        elif key == "space":
            if self.state in ("ready", "paused"):
                self.state = "playing"
                self._draw()
            elif self.state == "over":
                self.new_game()
        elif key in DIRECTIONS:
            self._turn(DIRECTIONS[key])

    def _turn(self, new_dir):
        if self.state == "ready":
            self.state = "playing"      # 방향키를 누르면 바로 시작
        if self.state != "playing":
            return
        p = self.human
        last = p.queue[-1] if p.queue else p.direction
        opposite = (-last[0], -last[1])
        # 빠른 연타로 제자리 되돌아 죽는 것을 막기 위해 입력을 최대 2개까지만 쌓는다
        if new_dir in (last, opposite) or len(p.queue) >= 2:
            return
        p.queue.append(new_dir)

    # ------------------------------------------------------------ 메인 루프
    def _tick(self):
        if self.state == "playing":
            self._step()
            self._draw()
        self.root.after(self.delay, self._tick)

    def _step(self):
        human, ai = self.human, self.ai
        players = (human, ai)

        if human.queue:
            human.direction = human.queue.pop(0)
        ai.direction = self._ai_direction()

        # 두 뱀은 동시에 움직이므로 새 머리 위치를 먼저 모두 계산한 뒤 충돌을 판정한다
        heads, eating = {}, {}
        for p in players:
            hx, hy = p.body[0]
            heads[p] = (hx + p.direction[0], hy + p.direction[1])
            eating[p] = heads[p] in self.foods

        dead = {}
        for p in players:
            other = ai if p is human else human
            head = heads[p]
            # 꼬리는 이번 칸에서 빠지므로(사과를 먹을 때는 제외) 꼬리 자리로는 들어갈 수 있다
            own = p.body if eating[p] else p.body[:-1]
            theirs = other.body if eating[other] else other.body[:-1]
            dead[p] = (
                not (0 <= head[0] < COLS and 0 <= head[1] < ROWS)
                or head in own
                or head in theirs
                or head == heads[other]     # 머리끼리 충돌하면 둘 다 죽는다
            )

        for p in players:
            if dead[p]:
                p.alive = False
                continue
            p.body.insert(0, heads[p])
            if eating[p]:
                p.apples += 1
                self.foods.remove(heads[p])
            else:
                p.body.pop()

        self._refill_foods()
        total = human.apples + ai.apples
        self.delay = max(DELAY_MIN, DELAY_START - total * DELAY_STEP)
        self._judge()

    def _judge(self):
        human, ai = self.human, self.ai
        if not human.alive or not ai.alive:
            if human.alive:
                winner, self.reason = human, "AI 충돌"
            elif ai.alive:
                winner, self.reason = ai, "YOU 충돌"
            else:
                winner, self.reason = self._leader(), "둘 다 충돌"
        elif max(human.apples, ai.apples) >= TARGET:
            winner, self.reason = self._leader(), f"먼저 {TARGET}개"
        else:
            return

        self.state = "over"
        self.winner = winner
        if winner is not None:
            self.wins[winner.name] += 1

    def _leader(self):
        """사과를 더 많이 먹은 쪽. 같으면 None(무승부)."""
        if self.human.apples == self.ai.apples:
            return None
        return self.human if self.human.apples > self.ai.apples else self.ai

    # ------------------------------------------------------------ AI
    @staticmethod
    def _bfs(start, blocked):
        """start 에서 막히지 않은 칸으로 갈 수 있는 모든 칸까지의 최단 거리."""
        dist = {start: 0}
        queue = deque([start])
        while queue:
            x, y = queue.popleft()
            for dx, dy in DIR_LIST:
                nxt = (x + dx, y + dy)
                if (0 <= nxt[0] < COLS and 0 <= nxt[1] < ROWS
                        and nxt not in blocked and nxt not in dist):
                    dist[nxt] = dist[(x, y)] + 1
                    queue.append(nxt)
        return dist

    def _ai_direction(self):
        me, foe = self.ai, self.human
        head = me.body[0]
        # 내 꼬리는 내가 움직이면 비워지므로 막힌 칸에서 뺀다. 상대 몸은 상대가 사과를 먹을 수도 있어 전부 막힌 칸.
        blocked = set(me.body[:-1]) | set(foe.body)
        fx, fy = foe.body[0]
        danger = {(fx + dx, fy + dy) for dx, dy in DIR_LIST}    # 상대 머리가 다음에 들어올 수 있는 칸

        back = (-me.direction[0], -me.direction[1])
        moves = []
        for d in DIR_LIST:
            cell = (head[0] + d[0], head[1] + d[1])
            if d != back and 0 <= cell[0] < COLS and 0 <= cell[1] < ROWS and cell not in blocked:
                moves.append((d, cell))
        if not moves:
            return me.direction      # 빠져나갈 곳이 없다

        # 노릴 사과: 상대보다 먼저(또는 동시에) 닿을 수 있는 것 중 가장 가까운 것, 없으면 그냥 가장 가까운 것
        dist_head = self._bfs(head, blocked)
        reachable = [f for f in self.foods if f in dist_head]
        dist_target = {}
        if reachable:
            contested = [f for f in reachable
                         if dist_head[f] <= abs(f[0] - fx) + abs(f[1] - fy)]
            target = min(contested or reachable, key=lambda f: dist_head[f])
            dist_target = self._bfs(target, blocked)

        def score(move):
            d, cell = move
            space = len(self._bfs(cell, blocked))
            safe = space >= len(me.body)     # 몸 길이보다 좁은 곳으로 들어가면 갇히기 쉽다
            return (
                not safe,
                0 if safe else -space,       # 안전한 길이 없으면 그나마 가장 넓은 쪽
                cell in danger,
                dist_target.get(cell, 999),
                d != me.direction,           # 같은 조건이면 직진
                random.random(),
            )

        return min(moves, key=score)[0]

    # ------------------------------------------------------------ 그리기
    def _cell_rect(self, x, y, pad=0):
        px, py = x * CELL, HUD_H + y * CELL
        return px + pad, py + pad, px + CELL - pad, py + CELL - pad

    def _draw(self):
        c = self.canvas
        c.delete("all")

        for p, x, anchor in ((self.human, 16, "w"), (self.ai, WIDTH - 16, "e")):
            head_color = p.colors[0]
            c.create_text(
                x, HUD_H / 2, anchor=anchor, fill=head_color, font=(FONT, 14, "bold"),
                text=f"{p.name} {p.apples}/{TARGET}  ({self.wins[p.name]}승)",
            )
        c.create_text(
            WIDTH / 2, HUD_H / 2, fill="#8a8aa0", font=(FONT, 11),
            text=f"먼저 {TARGET}개!",
        )

        # 바둑판 무늬
        for x in range(COLS):
            for y in range(ROWS):
                if (x + y) % 2:
                    c.create_rectangle(*self._cell_rect(x, y), fill=GRID, outline="")

        for food in self.foods:
            c.create_oval(*self._cell_rect(*food, pad=3), fill=FOOD_COLOR, outline="")

        for p in (self.ai, self.human):
            head_color, body_color = p.colors if p.alive else DEAD_COLORS
            for i, (x, y) in enumerate(p.body):
                c.create_rectangle(
                    *self._cell_rect(x, y, pad=1),
                    fill=head_color if i == 0 else body_color, outline="",
                )

        if self.state == "ready":
            self._overlay("SNAKE  vs  AI", "방향키 또는 Space 로 시작")
        elif self.state == "paused":
            self._overlay("일시정지", "P 또는 Space 로 계속")
        elif self.state == "over":
            if self.winner is self.human:
                title = "YOU WIN!"
            elif self.winner is self.ai:
                title = "AI WIN"
            else:
                title = "DRAW"
            self._overlay(
                title,
                f"{self.reason}  ·  {self.human.apples} : {self.ai.apples}  ·  Space / R 로 재시작",
            )

    def _overlay(self, title, subtitle):
        cy = HUD_H + ROWS * CELL / 2
        self.canvas.create_rectangle(
            0, cy - 70, WIDTH, cy + 70, fill="#000000", outline="", stipple="gray50",
        )
        self.canvas.create_text(WIDTH / 2, cy - 14, text=title, fill=ACCENT, font=(FONT, 30, "bold"))
        self.canvas.create_text(WIDTH / 2, cy + 28, text=subtitle, fill=FG, font=(FONT, 14))


def main():
    root = tk.Tk()
    Snake(root)
    root.mainloop()


if __name__ == "__main__":
    main()
