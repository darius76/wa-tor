#!/usr/bin/python3

import sys
import random
from collections import deque
from typing import Optional, Dict
import json
import os

try:
    import pygame
    from pygame.locals import QUIT, KEYDOWN, K_ESCAPE, MOUSEBUTTONDOWN, MOUSEBUTTONUP, MOUSEMOTION
except ImportError as exc:
    print("Missing dependency. Install with: pip install pygame")
    raise SystemExit(exc)

from wator_world import CreatureType, WaTorWorld

# torus renderer imported lazily to avoid OpenGL/pygame init conflict
_torus_available: Optional[bool] = None


def _check_torus() -> bool:
    """Return True if the torus renderer can be used (lazy import)."""
    global _torus_available
    if _torus_available is None:
        try:
            from torus_renderer import HAS_OPENGL as _has_gl  # noqa: F811
            _torus_available = _has_gl
        except ImportError:
            _torus_available = False
    return _torus_available

# settings persistence
SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "wator_settings.json")


def load_settings() -> Dict:
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_settings(data: Dict) -> None:
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except Exception:
        pass

# Default layout and UI constants (will be recomputed when world size changes)
WINDOW_WIDTH = 1000
WINDOW_HEIGHT = 760
GRID_X = 20
GRID_Y = 20
CELL_SIZE = 17
DEFAULT_COLS = 40
DEFAULT_ROWS = 30
BUTTON_WIDTH = 180

# Colors and UI
COLOR_EMPTY = (18, 20, 28)
SPECIES_COLORS = {
    "fish": (64, 160, 255),
    "shark": (255, 90, 90),
    "plant": (120, 200, 110),
}
BACKGROUND_COLOR = (22, 24, 32)
GRID_LINE_COLOR = (40, 40, 40)
PANEL_COLOR = (34, 38, 50)
BUTTON_COLOR = (80, 90, 110)
BUTTON_HOVER_COLOR = (100, 120, 160)
TEXT_COLOR = (232, 232, 232)
OVERLAY_BG = (20, 24, 32, 220)
SLIDER_BG = (60, 70, 90)
SLIDER_FG = (200, 200, 220)
DROPDOWN_BG = (28, 32, 40)
DROPDOWN_BORDER = (100, 110, 130)


# --- small UI widgets ---
class Button:
    def __init__(self, rect: pygame.Rect, label: str) -> None:
        self.rect = rect
        self.label = label

    def draw(self, surface: pygame.Surface, font: pygame.font.Font, active: bool = False) -> None:
        color = BUTTON_HOVER_COLOR if active else BUTTON_COLOR
        pygame.draw.rect(surface, color, self.rect, border_radius=8)
        draw_text(surface, self.label, (self.rect.x + 14, self.rect.y + 10), font)

    def hit(self, pos: tuple[int, int]) -> bool:
        return self.rect.collidepoint(pos)


class Slider:
    def __init__(self, rect: pygame.Rect, min_value: float, max_value: float, initial: float, is_int: bool = False) -> None:
        self.rect = rect
        self.min_value = min_value
        self.max_value = max_value
        self.value = initial
        self.dragging = False
        self.is_int = is_int

    def draw(self, surface: pygame.Surface, font: pygame.font.Font, label: str) -> None:
        pygame.draw.rect(surface, SLIDER_BG, self.rect, border_radius=6)
        track_rect = pygame.Rect(self.rect.x + 10, self.rect.centery - 4, self.rect.width - 20, 8)
        pygame.draw.rect(surface, (50, 60, 78), track_rect, border_radius=4)
        handle_x = self.snap_handle_x()
        pygame.draw.circle(surface, SLIDER_FG, (handle_x, track_rect.centery), 8)
        # show integers for integer sliders, otherwise show floats with 2 decimals
        if self.is_int:
            display_value = str(int(round(self.value)))
        else:
            display_value = f"{self.value:.2f}" if isinstance(self.value, float) else str(self.value)
        draw_text(surface, f"{label}: {display_value}", (self.rect.x + 10, self.rect.y - 22), font)

    def hit(self, pos: tuple[int, int]) -> bool:
        return self.rect.collidepoint(pos)

    def set_value_from_pos(self, pos: tuple[int, int]) -> None:
        track_x = self.rect.x + 10
        track_w = self.rect.width - 20
        value = self.min_value + ((pos[0] - track_x) / max(1, track_w)) * (self.max_value - self.min_value)
        value = max(self.min_value, min(self.max_value, value))
        if self.is_int:
            self.value = int(round(value))
        else:
            self.value = value

    def snap_handle_x(self) -> int:
        """Return the pixel X of the handle, snapped to integer steps if needed."""
        track_x = self.rect.x + 10
        track_w = self.rect.width - 20
        fraction = (self.value - self.min_value) / max(1e-6, self.max_value - self.min_value)
        return int(track_x + fraction * track_w)


class Dropdown:
    def __init__(self, pos: tuple[int, int], options: list[str]) -> None:
        self.option_rects = []
        self.options = options
        self.position = pos
        for i, _ in enumerate(options):
            self.option_rects.append(pygame.Rect(pos[0], pos[1] + i * 30, 140, 28))

    def draw(self, surface: pygame.Surface, font: pygame.font.Font) -> None:
        for option, rect in zip(self.options, self.option_rects):
            pygame.draw.rect(surface, DROPDOWN_BG, rect)
            pygame.draw.rect(surface, DROPDOWN_BORDER, rect, 1)
            draw_text(surface, option.title(), (rect.x + 8, rect.y + 5), font)

    def hit(self, pos: tuple[int, int]) -> Optional[str]:
        for option, rect in zip(self.options, self.option_rects):
            if rect.collidepoint(pos):
                return option
        return None


def draw_text(surface: pygame.Surface, text: str, pos: tuple[int, int], font: pygame.font.Font, color: tuple[int, int, int] = TEXT_COLOR) -> None:
    surface.blit(font.render(text, True, color), pos)


# --- world/build helpers ---
def initial_species() -> Dict[str, CreatureType]:
    # provide reasonable default spawn rates so empty cells can be refilled
    return {
        "fish": CreatureType(name="fish", diet={"plant"}, max_age=18, peak_age_range=(4, 12), base_skill=0.78, starvation_limit=5, age_skill_dropoff=0.55, spawn_rate=2.0, hunger_increase_factor=2.0),
        "shark": CreatureType(name="shark", diet={"fish"}, max_age=28, peak_age_range=(6, 16), base_skill=0.70, starvation_limit=6, age_skill_dropoff=0.45, spawn_rate=0.5, hunger_increase_factor=2.0),
        "plant": CreatureType(name="plant", diet=set(), max_age=8, peak_age_range=(1, 4), base_skill=0.95, starvation_limit=999, age_skill_dropoff=0.25, spawn_rate=8.0, hunger_increase_factor=1.0),
    }


def build_world_with_settings(species: Dict[str, CreatureType], cols: int, rows: int, fish_pct: float, shark_pct: float, plant_pct: float) -> WaTorWorld:
    world = WaTorWorld(cols, rows, species=species)
    for x, y in world.each_position():
        r = random.random() * 100.0
        if r < shark_pct:
            world.set(x, y, world.create_creature("shark", age=random.randint(0, 8), hunger=random.randint(0, 3)))
        elif r < shark_pct + fish_pct:
            world.set(x, y, world.create_creature("fish", age=random.randint(0, 6), hunger=random.randint(0, 3)))
        elif r < shark_pct + fish_pct + plant_pct:
            world.set(x, y, world.create_creature("plant", age=random.randint(0, 3)))
    return world


# --- layout recompute helper ---
def recompute_layout(cols: int, rows: int):
    global GRID_X, GRID_Y, CELL_SIZE, GRID_WIDTH, GRID_HEIGHT, PANEL_X, GRID_RECT, SETTINGS_BUTTON_RECT, RESET_BUTTON_RECT, EDIT_BUTTON_RECT
    GRID_WIDTH = CELL_SIZE * cols
    GRID_HEIGHT = CELL_SIZE * rows
    PANEL_X = GRID_X + GRID_WIDTH + 20
    GRID_RECT = pygame.Rect(GRID_X, GRID_Y, GRID_WIDTH, GRID_HEIGHT)
    SETTINGS_BUTTON_RECT = pygame.Rect(PANEL_X, 30, 180, 40)
    RESET_BUTTON_RECT = pygame.Rect(PANEL_X, 90, 180, 40)
    EDIT_BUTTON_RECT = pygame.Rect(PANEL_X, 150, 180, 40)


# initial layout
recompute_layout(DEFAULT_COLS, DEFAULT_ROWS)
OVERLAY_OK_RECT = pygame.Rect(320, 610, 120, 36)
OVERLAY_CANCEL_RECT = pygame.Rect(520, 610, 120, 36)


# --- main application ---
def main() -> None:
    pygame.init()
    # always use OpenGL display – avoids segfault when switching modes
    screen = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT),
                                     pygame.OPENGL | pygame.DOUBLEBUF)
    pygame.display.set_caption("Wa-Tor 2D Grid")
    font = pygame.font.Font(None, 22)
    clock = pygame.time.Clock()

    # 2D offscreen surface: all pygame 2D UI is drawn here, then uploaded
    # as an OpenGL texture each frame – avoids SRCALPHA / border_radius bugs
    # that occur when mixing pygame 2D drawing directly on an OpenGL surface.
    offscreen_2d = pygame.Surface((WINDOW_WIDTH, WINDOW_HEIGHT))

    # import OpenGL *after* display creation (doing it before causes segfault)
    from OpenGL.GL import (
        glClear, glClearColor, glBegin, glEnd, glColor3f, glVertex2f,
        glTexCoord2f, glMatrixMode, glLoadIdentity,
        GL_COLOR_BUFFER_BIT, GL_DEPTH_BUFFER_BIT, GL_QUADS,
        GL_PROJECTION, GL_MODELVIEW, glEnable, glDisable, GL_DEPTH_TEST,
        GL_TEXTURE_2D, GL_RGBA, GL_UNSIGNED_BYTE, GL_LINEAR,
        GL_TEXTURE_MIN_FILTER, GL_TEXTURE_MAG_FILTER,
        glViewport, glGenTextures, glDeleteTextures,
        glBindTexture, glTexImage2D, glTexParameteri, glFlush,
    )
    from OpenGL.GLU import gluOrtho2D

    def _setup_2d_ortho() -> None:
        """Ortho projection matching window for fullscreen quad."""
        glDisable(GL_DEPTH_TEST)
        glViewport(0, 0, WINDOW_WIDTH, WINDOW_HEIGHT)
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        gluOrtho2D(0, WINDOW_WIDTH, WINDOW_HEIGHT, 0)
        glMatrixMode(GL_MODELVIEW)
        glLoadIdentity()

    def _draw_offscreen_as_texture(surf: pygame.Surface) -> None:
        """Upload a pygame surface as an OpenGL texture and draw a fullscreen quad."""
        data = pygame.image.tostring(surf, "RGBA", False)  # False = top→bottom, matches ortho Y-down
        w, h = surf.get_size()

        tid = glGenTextures(1)
        glEnable(GL_TEXTURE_2D)
        glBindTexture(GL_TEXTURE_2D, tid)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0, GL_RGBA,
                     GL_UNSIGNED_BYTE, data)

        _setup_2d_ortho()
        glClearColor(0, 0, 0, 1)
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)

        glColor3f(1.0, 1.0, 1.0)
        glBegin(GL_QUADS)
        glTexCoord2f(0, 0); glVertex2f(0, 0)
        glTexCoord2f(1, 0); glVertex2f(w, 0)
        glTexCoord2f(1, 1); glVertex2f(w, h)
        glTexCoord2f(0, 1); glVertex2f(0, h)
        glEnd()

        glDisable(GL_TEXTURE_2D)
        glDeleteTextures([tid])

    settings = load_settings()
    species = initial_species()
    # apply saved species parameters if present
    if isinstance(settings, dict) and settings.get("species"):
        for name, vals in settings.get("species", {}).items():
            if name in species and isinstance(vals, dict):
                sp = species[name]
                if "base_skill" in vals:
                    sp.base_skill = float(vals["base_skill"])
                if "peak_age_range" in vals and isinstance(vals["peak_age_range"], list):
                    try:
                        low, high = vals["peak_age_range"]
                        sp.peak_age_range = (int(low), int(high))
                    except Exception:
                        pass
                if "spawn_rate" in vals:
                    sp.spawn_rate = float(vals["spawn_rate"])
                if "hunger_increase_factor" in vals:
                    sp.hunger_increase_factor = float(vals["hunger_increase_factor"])

    # read saved world size, initial percentages, and simulation speed
    saved_sliders = settings.get("sliders", {}) if isinstance(settings, dict) else {}
    if not isinstance(saved_sliders, dict):
        saved_sliders = {}
    cols = int(saved_sliders.get("cols", DEFAULT_COLS))
    rows = int(saved_sliders.get("rows", DEFAULT_ROWS))
    fish_pct = float(saved_sliders.get("fish_init", 12.0))
    shark_pct = float(saved_sliders.get("shark_init", 6.0))
    plant_pct = float(saved_sliders.get("plant_init", 47.0))
    sim_speed = float(settings.get("simulation_speed", 10.0))

    # initial world built from saved (or default) settings
    world = build_world_with_settings(species, cols, rows, fish_pct, shark_pct, plant_pct)
    world.simulation_speed = sim_speed
    recompute_layout(cols, rows)

    overlay_open = False
    edit_mode = False
    torus_mode = False
    graph_mode = False
    torus: Optional[TorusRenderer] = None
    pop_history: deque = deque(maxlen=300)  # (fish, shark, plant) counts per frame
    dropdown: Optional[Dropdown] = None
    current_cell: Optional[tuple[int, int]] = None
    menu_options = ["fish", "shark", "plant", "empty"]

    # sliders: speed, skill/peak, hunger factor, world size, initial percentages
    # Y positions compacted to fit 14 sliders + OK/Cancel buttons within the overlay
    saved = settings.get("sliders", {}) if isinstance(settings, dict) else {}
    sliders = {
        # ---- left column (X=60) ----
        "speed":        Slider(pygame.Rect(60, 95, 340, 28), 1, 20, float(saved.get("speed", world.simulation_speed if hasattr(world, 'simulation_speed') else 10.0)), is_int=False),
        "fish_skill":   Slider(pygame.Rect(60, 148, 340, 28), 0.0, 1.0, float(saved.get("fish_skill", world.species["fish"].base_skill))),
        "fish_peak":    Slider(pygame.Rect(60, 201, 340, 28), 6, 18, int(saved.get("fish_peak", world.species["fish"].peak_age_range[1])), is_int=True),
        "shark_skill":  Slider(pygame.Rect(60, 254, 340, 28), 0.0, 1.0, float(saved.get("shark_skill", world.species["shark"].base_skill))),
        "shark_peak":   Slider(pygame.Rect(60, 307, 340, 28), 6, 24, int(saved.get("shark_peak", world.species["shark"].peak_age_range[1])), is_int=True),
        "hunger_factor":Slider(pygame.Rect(60, 360, 340, 28), 0.5, 4.0, float(saved.get("hunger_factor", world.species["fish"].hunger_increase_factor))),
        "spawn_fish":   Slider(pygame.Rect(60, 413, 340, 28), 0.0, 50.0, float(saved.get("spawn_fish", world.species["fish"].spawn_rate if hasattr(world.species["fish"], 'spawn_rate') else 2.0))),
        "spawn_shark":  Slider(pygame.Rect(60, 466, 340, 28), 0.0, 25.0, float(saved.get("spawn_shark", world.species["shark"].spawn_rate if hasattr(world.species["shark"], 'spawn_rate') else 0.5))),
        "spawn_plant":  Slider(pygame.Rect(60, 519, 340, 28), 0.0, 100.0, float(saved.get("spawn_plant", world.species["plant"].spawn_rate if hasattr(world.species["plant"], 'spawn_rate') else 8.0))),
        # ---- right column (X=460) ----
        "cols":         Slider(pygame.Rect(460, 95, 260, 28), 10, 80, int(saved.get("cols", DEFAULT_COLS)), is_int=True),
        "rows":         Slider(pygame.Rect(460, 148, 260, 28), 8, 60, int(saved.get("rows", DEFAULT_ROWS)), is_int=True),
        "fish_init":    Slider(pygame.Rect(460, 201, 260, 28), 0.0, 100.0, float(saved.get("fish_init", 12.0))),
        "shark_init":   Slider(pygame.Rect(460, 254, 260, 28), 0.0, 100.0, float(saved.get("shark_init", 6.0))),
        "plant_init":   Slider(pygame.Rect(460, 307, 260, 28), 0.0, 100.0, float(saved.get("plant_init", 47.0))),
    }
    pending_sliders = {k: Slider(s.rect.copy(), s.min_value, s.max_value, s.value, getattr(s, 'is_int', False)) for k, s in sliders.items()}
    dragging_slider: Optional[Slider] = None

    running = True
    while running:
        mouse_pos = pygame.mouse.get_pos()
        for event in pygame.event.get():
            if event.type == QUIT:
                running = False
            elif torus_mode and torus is not None and torus.handle_event(event):
                continue
            elif event.type == KEYDOWN and event.key == K_ESCAPE:
                running = False
            elif event.type == KEYDOWN and event.key == pygame.K_t and _check_torus():
                torus_mode = not torus_mode
                if torus_mode:
                    from torus_renderer import TorusRenderer
                    pygame.display.set_caption("Wa-Tor – Torus View  |  T=toggle  |  drag=rotate  |  scroll=zoom  |  Esc=quit")
                    torus = TorusRenderer(world)
                    torus.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
                else:
                    torus = None
                    pygame.display.set_caption("Wa-Tor 2D Grid")
            elif event.type == KEYDOWN and event.key == pygame.K_g:
                graph_mode = not graph_mode
                if not graph_mode:
                    pop_history.clear()
            elif event.type == KEYDOWN and event.key == pygame.K_s and not torus_mode:
                # toggle settings overlay via S hotkey
                if overlay_open:
                    pending_sliders = {k: Slider(s.rect.copy(), s.min_value, s.max_value, sliders[k].value, getattr(s, 'is_int', False)) for k, s in sliders.items()}
                    overlay_open = False
                else:
                    pending_sliders = {k: Slider(s.rect.copy(), s.min_value, s.max_value, s.value, getattr(s, 'is_int', False)) for k, s in sliders.items()}
                    overlay_open = True
            elif event.type == MOUSEBUTTONDOWN and event.button == 1:
                if overlay_open:
                    if OVERLAY_OK_RECT.collidepoint(event.pos):
                        # apply changes
                        # update species parameters (allow float simulation speed)
                        world.simulation_speed = pending_sliders["speed"].value
                        world.species["fish"].base_skill = pending_sliders["fish_skill"].value
                        world.species["fish"].peak_age_range = (max(1, int(round(pending_sliders["fish_peak"].value)) - 6), int(round(pending_sliders["fish_peak"].value)))
                        world.species["shark"].base_skill = pending_sliders["shark_skill"].value
                        world.species["shark"].peak_age_range = (max(1, int(round(pending_sliders["shark_peak"].value)) - 6), int(round(pending_sliders["shark_peak"].value)))
                        # hunger factor applied to all species for simplicity
                        hf = pending_sliders["hunger_factor"].value
                        for sp in world.species.values():
                            sp.hunger_increase_factor = hf

                        # rebuild world if size or initial percentages changed
                        cols = int(round(pending_sliders["cols"].value))
                        rows = int(round(pending_sliders["rows"].value))
                        fish_pct = pending_sliders["fish_init"].value
                        shark_pct = pending_sliders["shark_init"].value
                        plant_pct = pending_sliders["plant_init"].value

                        # normalize percentages if sum > 100
                        total = fish_pct + shark_pct + plant_pct
                        if total > 100.0:
                            factor = 100.0 / max(1.0, total)
                            fish_pct *= factor
                            shark_pct *= factor
                            plant_pct *= factor

                        # recompute layout and rebuild
                        recompute_layout(cols, rows)
                        world = build_world_with_settings(world.species, cols, rows, fish_pct, shark_pct, plant_pct)
                        # set simulation speed on new world (allow float)
                        world.simulation_speed = pending_sliders["speed"].value
                        # apply spawn rates from pending sliders to species
                        if "spawn_fish" in pending_sliders:
                            world.species["fish"].spawn_rate = pending_sliders["spawn_fish"].value
                        if "spawn_shark" in pending_sliders:
                            world.species["shark"].spawn_rate = pending_sliders["spawn_shark"].value
                        if "spawn_plant" in pending_sliders:
                            world.species["plant"].spawn_rate = pending_sliders["spawn_plant"].value
                        # persist settings to disk
                        try:
                            saved_sliders = {k: (int(s.value) if getattr(s, 'is_int', False) else float(s.value)) for k, s in pending_sliders.items()}
                        except Exception:
                            saved_sliders = {k: s.value for k, s in pending_sliders.items()}
                        saved_species = {}
                        for name, sp in world.species.items():
                            saved_species[name] = {
                                "base_skill": float(sp.base_skill),
                                "peak_age_range": [int(sp.peak_age_range[0]), int(sp.peak_age_range[1])],
                                "spawn_rate": float(getattr(sp, 'spawn_rate', 0.0)),
                                "hunger_increase_factor": float(getattr(sp, 'hunger_increase_factor', 1.0)),
                            }
                        save_settings({"simulation_speed": float(world.simulation_speed), "sliders": saved_sliders, "species": saved_species})

                        # update active sliders so changes persist during this run
                        for k, s in pending_sliders.items():
                            if k in sliders:
                                sliders[k].value = s.value

                        pending_sliders = {k: Slider(s.rect.copy(), s.min_value, s.max_value, s.value, getattr(s, 'is_int', False)) for k, s in pending_sliders.items()}
                        overlay_open = False
                    elif OVERLAY_CANCEL_RECT.collidepoint(event.pos):
                        pending_sliders = {k: Slider(s.rect.copy(), s.min_value, s.max_value, sliders[k].value) for k, s in sliders.items()}
                        overlay_open = False
                    else:
                        # start dragging a slider
                        for slider in pending_sliders.values():
                            if slider.hit(event.pos):
                                slider.dragging = True
                                slider.set_value_from_pos(event.pos)
                                dragging_slider = slider
                                break
                else:
                    if SETTINGS_BUTTON_RECT.collidepoint(event.pos):
                        pending_sliders = {k: Slider(s.rect.copy(), s.min_value, s.max_value, s.value, getattr(s, 'is_int', False)) for k, s in sliders.items()}
                        overlay_open = True
                    elif RESET_BUTTON_RECT.collidepoint(event.pos):
                        # reset world to current sliders values
                        cols = int(round(sliders["cols"].value))
                        rows = int(round(sliders["rows"].value))
                        fish_pct = sliders["fish_init"].value
                        shark_pct = sliders["shark_init"].value
                        plant_pct = sliders["plant_init"].value
                        recompute_layout(cols, rows)
                        world = build_world_with_settings(world.species, cols, rows, fish_pct, shark_pct, plant_pct)
                        world.simulation_speed = sliders["speed"].value
                        # apply spawn rates from current sliders
                        if "spawn_fish" in sliders:
                            world.species["fish"].spawn_rate = sliders["spawn_fish"].value
                        if "spawn_shark" in sliders:
                            world.species["shark"].spawn_rate = sliders["spawn_shark"].value
                        if "spawn_plant" in sliders:
                            world.species["plant"].spawn_rate = sliders["spawn_plant"].value
                    elif EDIT_BUTTON_RECT.collidepoint(event.pos):
                        edit_mode = not edit_mode
                        dropdown = None
                    elif edit_mode and GRID_RECT.collidepoint(event.pos):
                        grid_x = (event.pos[0] - GRID_X) // CELL_SIZE
                        grid_y = (event.pos[1] - GRID_Y) // CELL_SIZE
                        if 0 <= grid_x < world.width and 0 <= grid_y < world.height:
                            current_cell = (grid_x, grid_y)
                            dropdown = Dropdown((event.pos[0], event.pos[1]), menu_options)
            elif event.type == MOUSEBUTTONUP and event.button == 1:
                if dragging_slider is not None:
                    dragging_slider.dragging = False
                    dragging_slider = None
                if dropdown is not None and current_cell is not None:
                    selection = dropdown.hit(event.pos)
                    if selection is not None:
                        x, y = current_cell
                        if selection == "empty":
                            world.set(x, y, None)
                        else:
                            world.set(x, y, world.create_creature(selection, age=0, hunger=0))
                    dropdown = None
                    current_cell = None
            elif event.type == MOUSEMOTION:
                if dragging_slider is not None:
                    dragging_slider.set_value_from_pos(event.pos)

        if not overlay_open and not edit_mode:
            world.step()
            for x, y in world.each_position():
                cell = world.get(x, y)
                # try spawning each species based on its spawn_rate (percent per tick)
                for sp_name, sp in world.species.items():
                    base_rate = getattr(sp, 'spawn_rate', 0.0) / 100.0
                    # scale spawn probability with simulation speed to keep respawn noticeable
                    speed_scale = max(1.0, (getattr(world, 'simulation_speed', 1) / 5.0))
                    rate = min(1.0, base_rate * speed_scale)
                    if rate <= 0.0 or random.random() >= rate:
                        continue

                    # Plant can spawn anywhere (per your request)
                    if sp_name == "plant":
                        world.set(x, y, world.create_creature("plant", age=0))
                        break

                    # For fish/shark: only spawn into free or plant-occupied cell
                    if cell is None or (cell is not None and cell.species.name == "plant"):
                        # require at least one neighbor of same species
                        has_neighbor = False
                        for (_npos, neighbor) in world.neighbors(x, y):
                            if neighbor is not None and neighbor.species.name == sp_name:
                                has_neighbor = True
                                break
                        if has_neighbor:
                            world.set(x, y, world.create_creature(sp_name, age=0))
                            break

        # track population for graph
        if graph_mode:
            fish_n = sum(1 for p in world.each_position()
                         if (c := world.get(*p)) is not None and c.species.name == "fish")
            shark_n = sum(1 for p in world.each_position()
                          if (c := world.get(*p)) is not None and c.species.name == "shark")
            plant_n = sum(1 for p in world.each_position()
                          if (c := world.get(*p)) is not None and c.species.name == "plant")
            pop_history.append((fish_n, shark_n, plant_n))

        # draw
        if torus_mode and torus is not None:
            torus.render(world)
        else:
            # render 2D UI to offscreen surface, then upload as GL texture
            offscreen_2d.fill(BACKGROUND_COLOR)
            pygame.draw.rect(offscreen_2d, PANEL_COLOR,
                             (GRID_X - 4, GRID_Y - 4, GRID_WIDTH + 8, GRID_HEIGHT + 8),
                             border_radius=10)
            draw_grid(offscreen_2d, world)
            draw_sidebar(offscreen_2d, font, overlay_open, edit_mode, world)
            Button(SETTINGS_BUTTON_RECT, "Settings").draw(offscreen_2d, font, overlay_open)
            Button(RESET_BUTTON_RECT, "Reset world").draw(offscreen_2d, font, False)
            Button(EDIT_BUTTON_RECT, "Edit grid").draw(offscreen_2d, font, edit_mode)

            if dropdown is not None:
                dropdown.draw(offscreen_2d, font)

            if overlay_open:
                draw_overlay(offscreen_2d, font, pending_sliders)

            # population graph (overlaid at bottom when enabled)
            if graph_mode:
                gx, gy = 20, WINDOW_HEIGHT - 180
                gw, gh = WINDOW_WIDTH - 40, 130
                draw_graph(offscreen_2d, pop_history, gx, gy, gw, gh, font)

            # persistent hotkey bar at the very bottom
            draw_hotkey_bar(offscreen_2d, font)
            # compact hotkey reference in top-right corner
            draw_hotkey_overlay(offscreen_2d, font)

            _draw_offscreen_as_texture(offscreen_2d)

        pygame.display.flip()
        clock.tick(10 + int(world.simulation_speed * 2))

    # save current sliders and species on exit
    try:
        current_sliders = {k: (int(s.value) if getattr(s, 'is_int', False) else float(s.value)) for k, s in sliders.items()}
    except Exception:
        current_sliders = {k: s.value for k, s in sliders.items()}
    current_species = {}
    for name, sp in world.species.items():
        current_species[name] = {
            "base_skill": float(sp.base_skill),
            "peak_age_range": [int(sp.peak_age_range[0]), int(sp.peak_age_range[1])],
            "spawn_rate": float(getattr(sp, 'spawn_rate', 0.0)),
            "hunger_increase_factor": float(getattr(sp, 'hunger_increase_factor', 1.0)),
        }
    save_settings({"simulation_speed": float(getattr(world, 'simulation_speed', 0.0)), "sliders": current_sliders, "species": current_species})

    pygame.quit()
    sys.exit(0)


# --- drawing helpers that use dynamic world size ---
def draw_graph(surface: pygame.Surface, history: deque,
               x: int, y: int, w: int, h: int, font: pygame.font.Font) -> None:
    """Draw a sliding population line graph."""
    if len(history) < 2:
        return
    # background
    graph_bg = (28, 32, 42)
    pygame.draw.rect(surface, graph_bg, (x, y, w, h), border_radius=8)
    # subtle grid lines
    grid_col = (50, 55, 70)
    for i in range(1, 4):
        ly = int(y + i * h / 4)
        pygame.draw.line(surface, grid_col, (x + 8, ly), (x + w - 8, ly), 1)
    # determine max for scaling
    max_val = max(max(entry) for entry in history) or 1
    colors = {"fish": (64, 160, 255), "shark": (255, 90, 90), "plant": (120, 200, 110)}
    labels = {"fish": "Fish", "shark": "Shark", "plant": "Plant"}
    # draw legend
    lx = x + w - 170
    for i, (sp, col) in enumerate(colors.items()):
        ly2 = y + 10 + i * 18
        pygame.draw.line(surface, col, (lx, ly2 + 5), (lx + 16, ly2 + 5), 2)
        draw_text(surface, labels[sp], (lx + 22, ly2), font, col)
    # draw lines
    step = w / max(1, len(history) - 1)
    for sp_idx, (sp_name, color) in enumerate(colors.items()):
        points = []
        for i, entry in enumerate(history):
            px2 = x + int(i * step)
            py2 = int(y + h - 12 - (entry[sp_idx] / max_val) * (h - 30))
            points.append((px2, py2))
        if len(points) >= 2:
            pygame.draw.lines(surface, color, False, points, 2)


def draw_hotkey_bar(surface: pygame.Surface, font: pygame.font.Font) -> None:
    """Draw a persistent hotkey hint bar at the bottom of the window."""
    bar_rect = pygame.Rect(0, WINDOW_HEIGHT - 26, WINDOW_WIDTH, 26)
    pygame.draw.rect(surface, (18, 20, 28), bar_rect)
    pygame.draw.line(surface, (50, 55, 70), (0, WINDOW_HEIGHT - 26), (WINDOW_WIDTH, WINDOW_HEIGHT - 26), 1)
    hints = "T: Torus/2D  |  G: Graph  |  S: Settings  |  Esc: Quit  |  Drag: Rotate Torus  |  Scroll: Zoom"
    draw_text(surface, hints, (16, WINDOW_HEIGHT - 21), font, (160, 170, 190))


def draw_hotkey_overlay(surface: pygame.Surface, font: pygame.font.Font) -> None:
    """Draw a compact hotkey reference overlay in the top-right corner."""
    lines = [
        ("T", "Torus / 2D"),
        ("G", "Graph on/off"),
        ("S", "Settings"),
        ("Esc", "Quit"),
    ]
    line_h = 20
    padding = 10
    max_key_w = max(font.size(ln[0])[0] for ln in lines)
    max_desc_w = max(font.size(ln[1])[0] for ln in lines)
    box_w = max_key_w + max_desc_w + padding * 3 + 20
    box_h = len(lines) * line_h + padding * 2 + 8
    box_x = WINDOW_WIDTH - box_w - 16
    box_y = 4

    # semi-transparent background
    overlay_surf = pygame.Surface((box_w, box_h), pygame.SRCALPHA)
    overlay_surf.fill((18, 20, 28, 210))
    pygame.draw.rect(overlay_surf, (60, 65, 80, 180), overlay_surf.get_rect(), border_radius=8, width=1)
    surface.blit(overlay_surf, (box_x, box_y))

    key_x = box_x + padding
    desc_x = key_x + max_key_w + 12
    for i, (key, desc) in enumerate(lines):
        ly = box_y + padding + i * line_h + 4
        # key badge
        badge_rect = pygame.Rect(key_x - 2, ly - 1, max_key_w + 10, line_h)
        pygame.draw.rect(surface, (55, 60, 78), badge_rect, border_radius=4)
        draw_text(surface, key, (key_x + 3, ly), font, (200, 210, 230))
        # description
        draw_text(surface, desc, (desc_x, ly), font, (150, 160, 180))


def draw_grid(surface: pygame.Surface, world: WaTorWorld) -> None:
    for y in range(world.height):
        for x in range(world.width):
            creature = world.get(x, y)
            color = SPECIES_COLORS.get(creature.species.name, COLOR_EMPTY) if creature else COLOR_EMPTY
            cell_rect = pygame.Rect(GRID_X + x * CELL_SIZE, GRID_Y + y * CELL_SIZE, CELL_SIZE - 1, CELL_SIZE - 1)
            pygame.draw.rect(surface, color, cell_rect)
    for x in range(world.width + 1):
        line_x = GRID_X + x * CELL_SIZE
        pygame.draw.line(surface, GRID_LINE_COLOR, (line_x, GRID_Y), (line_x, GRID_Y + world.height * CELL_SIZE))
    for y in range(world.height + 1):
        line_y = GRID_Y + y * CELL_SIZE
        pygame.draw.line(surface, GRID_LINE_COLOR, (GRID_X, line_y), (GRID_X + world.width * CELL_SIZE, line_y))


def draw_sidebar(surface: pygame.Surface, font: pygame.font.Font, overlay_open: bool, edit_mode: bool, world: WaTorWorld) -> None:
    panel_rect = pygame.Rect(PANEL_X - 12, GRID_Y - 12, BUTTON_WIDTH + 24, GRID_HEIGHT + 24)
    pygame.draw.rect(surface, PANEL_COLOR, panel_rect, border_radius=12)
    draw_text(surface, "Controls", (PANEL_X, GRID_Y), font)
    draw_text(surface, f"Creatures: {sum(1 for _ in world.each_position() if world.get(*_) is not None)}", (PANEL_X, GRID_Y + 220), font)
    draw_text(surface, f"Edit mode: {'On' if edit_mode else 'Off'}", (PANEL_X, GRID_Y + 250), font)
    draw_text(surface, f"Overlay: {'Open' if overlay_open else 'Closed'}", (PANEL_X, GRID_Y + 280), font)
    if _check_torus():
        draw_text(surface, "Press T for torus view", (PANEL_X, GRID_Y + 310), font,
                  (150, 180, 220))


def draw_overlay(surface: pygame.Surface, font: pygame.font.Font, sliders: Dict[str, Slider]) -> None:
    overlay_rect = pygame.Rect(40, 40, WINDOW_WIDTH - 80, WINDOW_HEIGHT - 80)
    overlay_surface = pygame.Surface((overlay_rect.width, overlay_rect.height), pygame.SRCALPHA)
    overlay_surface.fill(OVERLAY_BG)
    surface.blit(overlay_surface, overlay_rect.topleft)

    draw_text(surface, "Simulation Settings", (overlay_rect.x + 24, overlay_rect.y + 24), font)
    draw_text(surface, "Adjust parameters below, then press OK or Cancel.", (overlay_rect.x + 24, overlay_rect.y + 56), font)

    # left column: simulation and species
    sliders["speed"].draw(surface, font, "Simulation speed")
    sliders["fish_skill"].draw(surface, font, "Fish skill")
    sliders["fish_peak"].draw(surface, font, "Fish peak age")
    sliders["shark_skill"].draw(surface, font, "Shark skill")
    sliders["shark_peak"].draw(surface, font, "Shark peak age")
    sliders["hunger_factor"].draw(surface, font, "Hunger increase factor")
    # spawn rate controls (percent per tick)
    sliders["spawn_fish"].draw(surface, font, "Spawn fish %/tick")
    sliders["spawn_shark"].draw(surface, font, "Spawn shark %/tick")
    sliders["spawn_plant"].draw(surface, font, "Spawn plant %/tick")

    # right column: world size and initial percentages
    sliders["cols"].draw(surface, font, "World columns")
    sliders["rows"].draw(surface, font, "World rows")
    sliders["fish_init"].draw(surface, font, "Initial fish %")
    sliders["shark_init"].draw(surface, font, "Initial shark %")
    sliders["plant_init"].draw(surface, font, "Initial plant %")

    pygame.draw.rect(surface, BUTTON_COLOR, OVERLAY_OK_RECT, border_radius=8)
    pygame.draw.rect(surface, BUTTON_COLOR, OVERLAY_CANCEL_RECT, border_radius=8)
    draw_text(surface, "OK", (OVERLAY_OK_RECT.x + 40, OVERLAY_OK_RECT.y + 10), font)
    draw_text(surface, "Cancel", (OVERLAY_CANCEL_RECT.x + 30, OVERLAY_CANCEL_RECT.y + 10), font)


if __name__ == "__main__":
    main()
