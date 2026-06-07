"""Torus renderer – maps the 2D Wa-Tor grid onto a 3D torus using PyOpenGL."""

import math
from typing import Optional, Tuple

import pygame
from pygame.locals import MOUSEBUTTONDOWN, MOUSEBUTTONUP, MOUSEMOTION

try:
    from OpenGL.GL import (
        glBegin, glEnd, glVertex3f, glColor3f, glClear, glEnable, glDisable,
        GL_COLOR_BUFFER_BIT, GL_DEPTH_BUFFER_BIT, GL_DEPTH_TEST,
        GL_QUADS, GL_LINES, GL_LINE_LOOP,
        glClearColor, glViewport, glMatrixMode, glLoadIdentity,
        GL_PROJECTION, GL_MODELVIEW,
        glRotatef, glTranslatef, glScalef, glLineWidth,
        glPushMatrix, glPopMatrix,
    )
    from OpenGL.GLU import gluPerspective
    HAS_OPENGL = True
except ImportError:
    HAS_OPENGL = False

from wator_world import WaTorWorld

# colour palette matching the 2D view
COLOR_EMPTY = (0.07, 0.08, 0.11)
CELL_COLORS = {
    "fish": (0.25, 0.63, 1.0),
    "shark": (1.0, 0.35, 0.35),
    "plant": (0.47, 0.78, 0.43),
}
GRID_LINE_COLOR = (0.25, 0.25, 0.25)
TORUS_BG = (0.06, 0.07, 0.10)


class TorusRenderer:
    """Renders the world as a textured parametric torus.

    Torus parameters (R = major radius, r = minor radius):
        x(θ, φ) = (R + r·cos θ)·cos φ
        y(θ, φ) = (R + r·cos θ)·sin φ
        z(θ, φ) = r·sin θ

    Grid mapping:
        column index  → φ  (major angle, around the big ring)
        row index     → θ  (minor angle, around the tube)
    """

    MAJOR_RADIUS = 3.0   # R
    MINOR_RADIUS = 1.0   # r

    def __init__(self, world: WaTorWorld) -> None:
        if not HAS_OPENGL:
            raise RuntimeError("PyOpenGL is required for 3D torus view.  "
                               "Install with: pip install PyOpenGL")

        self.world = world
        self.cols = world.width
        self.rows = world.height

        # view transforms
        self.rot_x: float = -30.0
        self.rot_y: float = 0.0
        self.rot_z: float = 15.0
        self.zoom: float = -8.0
        self._dragging = False
        self._last_mouse: Optional[Tuple[int, int]] = None

        # momentum / inertia
        self._vel_x: float = 0.0   # rotation velocity (degrees / frame) about X
        self._vel_y: float = 0.0   # rotation velocity about Y
        self._friction: float = 0.96  # per-frame damping (1.0 = no friction)

        # display size
        self._width: int = 800
        self._height: int = 600

    # ------------------------------------------------------------------
    # geometry helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _torus_point(theta: float, phi: float, R: float, r: float) -> Tuple[float, float, float]:
        ct, st = math.cos(theta), math.sin(theta)
        cp, sp = math.cos(phi), math.sin(phi)
        return ((R + r * ct) * cp, (R + r * ct) * sp, r * st)

    def resize(self, width: int, height: int) -> None:
        self._width = width
        self._height = height

    # ------------------------------------------------------------------
    # input handling (returns True if event was consumed)
    # ------------------------------------------------------------------
    def handle_event(self, event: pygame.event.Event) -> bool:
        if event.type == MOUSEBUTTONDOWN and event.button in (1, 3):
            self._dragging = True
            self._last_mouse = event.pos
            self._vel_x = 0.0
            self._vel_y = 0.0
            return True
        if event.type == MOUSEBUTTONUP and event.button in (1, 3):
            self._dragging = False
            self._last_mouse = None
            return True
        if event.type == MOUSEMOTION and self._dragging:
            if self._last_mouse is not None:
                dx = event.pos[0] - self._last_mouse[0]
                dy = event.pos[1] - self._last_mouse[1]
                # store only velocity; rotation is applied in render() for
                # consistent behaviour between drag and momentum phases
                self._vel_x = dy * 0.4
                self._vel_y = dx * 0.4
            self._last_mouse = event.pos
            return True
        if event.type == pygame.MOUSEWHEEL:
            self.zoom += event.y * 0.5
            return True
        return False

    # ------------------------------------------------------------------
    # render
    # ------------------------------------------------------------------
    def render(self, world: WaTorWorld) -> None:
        """Draw the torus with current world state (applies momentum)."""
        self.world = world
        self.cols = world.width
        self.rows = world.height

        # apply rotation from velocity (covers both drag and momentum)
        if abs(self._vel_x) > 0.01 or abs(self._vel_y) > 0.01:
            self.rot_x += self._vel_x
            self.rot_y += self._vel_y
            if not self._dragging:
                # decay only in momentum phase (not while dragging)
                self._vel_x *= self._friction
                self._vel_y *= self._friction
            # clamp tiny velocities to zero
            if abs(self._vel_x) < 0.02:
                self._vel_x = 0.0
            if abs(self._vel_y) < 0.02:
                self._vel_y = 0.0

        glClearColor(*TORUS_BG, 1.0)
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        glEnable(GL_DEPTH_TEST)

        glViewport(0, 0, self._width, self._height)
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        gluPerspective(45, self._width / max(1, self._height), 0.1, 60.0)

        glMatrixMode(GL_MODELVIEW)
        glLoadIdentity()
        glTranslatef(0, 0, self.zoom)
        glRotatef(self.rot_x, 1, 0, 0)
        glRotatef(self.rot_y, 0, 1, 0)
        glRotatef(self.rot_z, 0, 0, 1)

        R, r = self.MAJOR_RADIUS, self.MINOR_RADIUS

        # ---- cell quads ----
        for col in range(self.cols):
            phi0 = (col / self.cols) * 2 * math.pi
            phi1 = ((col + 1) / self.cols) * 2 * math.pi
            for row in range(self.rows):
                theta0 = (row / self.rows) * 2 * math.pi
                theta1 = ((row + 1) / self.rows) * 2 * math.pi

                creature = world.get(col, row)
                if creature is not None:
                    color = CELL_COLORS.get(creature.species.name, COLOR_EMPTY)
                else:
                    color = COLOR_EMPTY

                glColor3f(*color)
                glBegin(GL_QUADS)
                for t, p in [(theta0, phi0), (theta0, phi1),
                             (theta1, phi1), (theta1, phi0)]:
                    glVertex3f(*self._torus_point(t, p, R, r))
                glEnd()

        # ---- wireframe overlays (sub-grid every 5 cells for readability) ----
        glLineWidth(0.8)
        # major rings (constant θ)
        for row in range(0, self.rows + 1, max(1, self.rows // 8)):
            theta = (row / self.rows) * 2 * math.pi
            glColor3f(*GRID_LINE_COLOR)
            glBegin(GL_LINE_LOOP)
            for col in range(self.cols):
                phi = (col / self.cols) * 2 * math.pi
                glVertex3f(*self._torus_point(theta, phi, R, r))
            glEnd()

        # minor rings (constant φ)
        for col in range(0, self.cols + 1, max(1, self.cols // 12)):
            phi = (col / self.cols) * 2 * math.pi
            glColor3f(*GRID_LINE_COLOR)
            glBegin(GL_LINE_LOOP)
            for row in range(self.rows):
                theta = (row / self.rows) * 2 * math.pi
                glVertex3f(*self._torus_point(theta, phi, R, r))
            glEnd()

        glDisable(GL_DEPTH_TEST)

    # ------------------------------------------------------------------
    # overlay helpers (called after render, uses pygame for text)
    # ------------------------------------------------------------------
    def draw_overlay_text(self, surface: "pygame.Surface") -> None:
        """Draw control hints on a separate pygame surface (shown after GL frame)."""
        pass  # reserved for future use
