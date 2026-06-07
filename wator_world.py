from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union

Position = Tuple[int, int]
EnvironmentFood = "plant"


def clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(value, maximum))


@dataclass
class CreatureType:
    name: str
    diet: Set[str] = field(default_factory=set)
    max_age: int = 20
    peak_age_range: Tuple[int, int] = (4, 12)
    base_skill: float = 0.75
    starvation_limit: int = 5
    age_skill_dropoff: float = 0.5
    spawn_rate: float = 0.0
    hunger_increase_factor: float = 2.0

    def can_eat(self, prey: Optional[Creature]) -> bool:
        if prey is None:
            return False
        if prey.species.name == EnvironmentFood:
            return EnvironmentFood in self.diet
        return prey.species.name in self.diet

    def age_skill_modifier(self, age: int) -> float:
        low, high = self.peak_age_range
        if low <= age <= high:
            return 1.0
        distance = 0
        if age < low:
            distance = low - age
        else:
            distance = age - high
        reduction = (distance / max(1, high)) * self.age_skill_dropoff
        return clamp(1.0 - reduction)

    def death_by_age_probability(self, age: int) -> float:
        if age <= 0:
            return 0.0
        ratio = age / max(1, self.max_age)
        probability = clamp(ratio**2)
        return probability

    def hunger_drive(self, hunger: int) -> float:
        hunger_fraction = clamp(hunger / max(1, self.starvation_limit))
        return clamp(hunger_fraction**self.hunger_increase_factor)


@dataclass
class Creature:
    species: CreatureType
    age: int = 0
    hunger: int = 0
    custom_data: Dict[str, Any] = field(default_factory=dict)

    def is_starving(self) -> bool:
        return self.hunger >= self.species.starvation_limit

    def effective_skill(self) -> float:
        skill = self.species.base_skill * self.species.age_skill_modifier(self.age)
        return clamp(skill)

    def should_die_by_age(self) -> bool:
        probability = self.species.death_by_age_probability(self.age)
        survival_skill = 1.0 - self.effective_skill()
        return random.random() < clamp(probability * (1.0 + survival_skill))

    def should_die_by_starvation(self) -> bool:
        if self.hunger < self.species.starvation_limit:
            return False
        chance = clamp((self.hunger - self.species.starvation_limit + 1) / self.species.starvation_limit)
        return random.random() < chance

    def age_one_turn(self) -> None:
        self.age += 1
        self.hunger += 1

    def feed(self) -> None:
        self.hunger = max(0, self.hunger - 2)

    def hunger_drive(self) -> float:
        return self.species.hunger_drive(self.hunger)

    def wants_to_eat(self) -> bool:
        return random.random() < self.hunger_drive()


class WaTorWorld:
    def __init__(
        self,
        width: int,
        height: int,
        species: Optional[Dict[str, CreatureType]] = None,
        initial_cells: Optional[Sequence[Sequence[Optional[Union[Creature, str]]]]] = None,
    ) -> None:
        self.width = width
        self.height = height
        self.species = species or {}
        self.grid: List[List[Optional[Creature]]] = [
            [None for _ in range(width)] for _ in range(height)
        ]

        if initial_cells is not None:
            self.initialize_grid(initial_cells)

    def wrap(self, x: int, y: int) -> Position:
        return x % self.width, y % self.height

    def get(self, x: int, y: int) -> Optional[Creature]:
        x, y = self.wrap(x, y)
        return self.grid[y][x]

    def set(self, x: int, y: int, creature: Optional[Creature]) -> None:
        x, y = self.wrap(x, y)
        self.grid[y][x] = creature

    def initialize_grid(
        self,
        initial_cells: Sequence[Sequence[Optional[Union[Creature, str]]]],
    ) -> None:
        for y, row in enumerate(initial_cells):
            for x, value in enumerate(row):
                if y < self.height and x < self.width:
                    if isinstance(value, Creature):
                        self.grid[y][x] = value
                    elif isinstance(value, str):
                        creature = self.create_creature(value)
                        self.grid[y][x] = creature
                    else:
                        self.grid[y][x] = None

    def create_creature(self, species_name: str, **kwargs: Any) -> Optional[Creature]:
        species = self.species.get(species_name)
        if species is None:
            return None
        return Creature(species=species, **kwargs)

    def neighbors(self, x: int, y: int) -> Iterable[Tuple[Position, Optional[Creature]]]:
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nx, ny = self.wrap(x + dx, y + dy)
                yield (nx, ny), self.grid[ny][nx]

    def each_position(self) -> Iterable[Position]:
        for y in range(self.height):
            for x in range(self.width):
                yield x, y

    def step(self) -> None:
        # Movement-enabled step: creatures move, pursue/escape, and seek food when hungry
        old_grid = [row[:] for row in self.grid]
        new_grid: List[List[Optional[Creature]]] = [[None for _ in range(self.width)] for _ in range(self.height)]
        moved: Set[Position] = set()

        positions = list(self.each_position())
        random.shuffle(positions)

        def toroidal_distance(a: Position, b: Position) -> int:
            ax, ay = a
            bx, by = b
            dx = min(abs(ax - bx), self.width - abs(ax - bx))
            dy = min(abs(ay - by), self.height - abs(ay - by))
            return max(dx, dy)

        def nearest_creature_pos(x: int, y: int, target_species: str, max_search: int = 8) -> Optional[Position]:
            # Breadth-expanding ring search returning the first found (closest)
            for r in range(1, max_search + 1):
                candidates: List[Position] = []
                for dy in range(-r, r + 1):
                    for dx in range(-r, r + 1):
                        if abs(dx) != r and abs(dy) != r:
                            continue
                        nx, ny = self.wrap(x + dx, y + dy)
                        c = old_grid[ny][nx]
                        if c is not None and c.species.name == target_species:
                            candidates.append((nx, ny))
                if candidates:
                    # pick the closest by toroidal distance and randomize ties
                    random.shuffle(candidates)
                    return min(candidates, key=lambda p: toroidal_distance((x, y), p))
            return None

        for x, y in positions:
            if (x, y) in moved:
                continue
            creature = old_grid[y][x]
            if creature is None:
                continue

            creature.age_one_turn()
            if creature.should_die_by_age() or creature.should_die_by_starvation():
                # creature dies; leave cell empty
                continue

            nbrs = list(self.neighbors(x, y))

            # If creature wants to eat, prefer to move into prey cell and eat
            if creature.wants_to_eat():
                eaten = False
                # randomize checking order to avoid directional bias
                random.shuffle(nbrs)
                for (nx, ny), neighbor in nbrs:
                    if neighbor is None:
                        continue
                    if neighbor.species.name in creature.species.diet:
                        creature.feed()
                        new_grid[ny][nx] = creature
                        moved.add((nx, ny))
                        eaten = True
                        break
                if eaten:
                    continue

            # Collect empty neighbors (that are still free in new_grid)
            empty_neighbors = [(nx, ny) for (nx, ny), neighbor in nbrs if neighbor is None and new_grid[ny][nx] is None]
            random.shuffle(empty_neighbors)

            # Hunger-driven seeking: when hunger is high, seek nearest food target
            hunger_threshold = max(1, creature.species.starvation_limit // 2)
            if creature.hunger >= hunger_threshold:
                if creature.species.name == "fish":
                    target = nearest_creature_pos(x, y, "plant", max_search=10)
                    if target and empty_neighbors:
                        # choose neighbor minimizing distance to target
                        best = min(empty_neighbors, key=lambda pos: toroidal_distance(pos, target))
                        tx, ty = best
                        new_grid[ty][tx] = creature
                        moved.add((tx, ty))
                        continue
                if creature.species.name == "shark":
                    target = nearest_creature_pos(x, y, "fish", max_search=12)
                    if target and empty_neighbors:
                        best = min(empty_neighbors, key=lambda pos: toroidal_distance(pos, target))
                        tx, ty = best
                        new_grid[ty][tx] = creature
                        moved.add((tx, ty))
                        continue

            # Default escape/pursuit behaviour with randomness and tie-breaking
            if creature.species.name == "fish":
                # try to maximize distance from nearest shark
                if empty_neighbors:
                    # compute score for each empty neighbor; higher is better
                    best_cell = None
                    best_score = -1
                    for ex, ey in empty_neighbors:
                        shark_pos = nearest_creature_pos(ex, ey, "shark", max_search=8)
                        score = toroidal_distance((ex, ey), shark_pos) if shark_pos is not None else max(self.width, self.height)
                        if score > best_score or (score == best_score and random.random() < 0.5):
                            best_score = score
                            best_cell = (ex, ey)
                    if best_cell is not None:
                        tx, ty = best_cell
                        new_grid[ty][tx] = creature
                        moved.add((tx, ty))
                        continue
                new_grid[y][x] = creature
                moved.add((x, y))
                continue

            if creature.species.name == "shark":
                # pursue fish by moving to neighbor that reduces distance
                if empty_neighbors:
                    best_cell = None
                    best_score = None
                    for ex, ey in empty_neighbors:
                        fish_pos = nearest_creature_pos(ex, ey, "fish", max_search=10)
                        score = toroidal_distance((ex, ey), fish_pos) if fish_pos is not None else max(self.width, self.height)
                        if best_score is None or score < best_score or (score == best_score and random.random() < 0.5):
                            best_score = score
                            best_cell = (ex, ey)
                    if best_cell is not None:
                        tx, ty = best_cell
                        new_grid[ty][tx] = creature
                        moved.add((tx, ty))
                        continue
                new_grid[y][x] = creature
                moved.add((x, y))
                continue

            # default: plants and other immobile species stay
            new_grid[y][x] = creature
            moved.add((x, y))

        # Commit new grid
        self.grid = new_grid

    def select_prey(self, x: int, y: int) -> Tuple[Optional[Position], bool]:
        creature = self.get(x, y)
        if creature is None:
            return None, False

        candidates: List[Tuple[int, Position]] = []
        for (nx, ny), neighbor in self.neighbors(x, y):
            if neighbor is None:
                continue
            if neighbor.species.name in creature.species.diet:
                score = neighbor.age / max(1, neighbor.species.max_age)
                candidates.append((score, (nx, ny)))
        if candidates:
            return max(candidates, key=lambda item: item[0])[1], False
        if EnvironmentFood in creature.species.diet:
            return None, True
        return None, False

    def set_species(self, species: CreatureType) -> None:
        self.species[species.name] = species

    def as_matrix(self) -> List[List[Optional[str]]]:
        return [
            [cell.species.name if cell is not None else None for cell in row]
            for row in self.grid
        ]

    def describe(self) -> str:
        lines = [f"WaTorWorld {self.width}x{self.height}"]
        for y in range(self.height):
            line = []
            for x in range(self.width):
                creature = self.get(x, y)
                line.append(creature.species.name[0] if creature else ".")
            lines.append("".join(line))
        return "\n".join(lines)
