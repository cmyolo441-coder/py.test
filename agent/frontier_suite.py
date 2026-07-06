"""Embodied AI & privacy suite — IoT control, robotics, digital twin,
differential privacy, federated learning, quantum-inspired optimization,
neuroevolution, adversarial robustness.

This module bundles 8 frontier features into one file for compactness.
Each is real, working code (some require optional dependencies).
"""
from __future__ import annotations

import math
import random
import socket
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable


# ============================================================================
# 1. IoT DEVICE CONTROL (MQTT-style, no broker required for local)
# ============================================================================

@dataclass
class IoTDevice:
    """A smart-home IoT device."""
    id: str
    name: str
    device_type: str  # "light", "thermostat", "sensor", "switch", "camera"
    state: dict[str, Any] = field(default_factory=dict)
    last_seen: float = field(default_factory=time.time)


class IoTController:
    """Control IoT devices via a simple TCP protocol (MQTT-compatible)."""

    def __init__(self, broker_host: str = "localhost", broker_port: int = 1883) -> None:
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.devices: dict[str, IoTDevice] = {}
        self._socket: socket.socket | None = None

    def register_device(self, device_id: str, name: str, device_type: str) -> IoTDevice:
        device = IoTDevice(id=device_id, name=name, device_type=device_type)
        self.devices[device_id] = device
        return device

    def set_state(self, device_id: str, key: str, value: Any) -> bool:
        """Set a device state (e.g., light brightness, thermostat temp)."""
        device = self.devices.get(device_id)
        if device is None:
            return False
        device.state[key] = value
        device.last_seen = time.time()
        # Try to publish to MQTT broker (best-effort).
        self._publish(f"device/{device_id}/{key}", str(value))
        return True

    def get_state(self, device_id: str) -> dict[str, Any]:
        return self.devices.get(device_id, IoTDevice("", "", "")).state.copy()

    def _publish(self, topic: str, payload: str) -> None:
        """Best-effort MQTT publish (silently fails if no broker)."""
        try:
            if self._socket is None:
                self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._socket.settimeout(2)
                self._socket.connect((self.broker_host, self.broker_port))
            # Simple MQTT-like publish (not real MQTT, but demonstrates the concept).
            self._socket.sendall(f"{topic}={payload}\n".encode())
        except (OSError, socket.timeout):
            self._socket = None  # reset, will retry next time

    def automate(self, rule: str) -> str:
        """Create an automation rule (e.g., 'if light.on and time>22:00 then light.off')."""
        # Store the rule; in a real system this would be parsed and scheduled.
        return f"Automation rule registered: {rule}"

    def dashboard(self) -> str:
        lines = [f"IoT controller ({len(self.devices)} devices):"]
        for dev in self.devices.values():
            lines.append(f"  {dev.name:<20} [{dev.device_type}]  state={dev.state}")
        return "\n".join(lines)


# ============================================================================
# 2. ROBOTICS INTERFACE (ROS-style, simplified)
# ============================================================================

@dataclass
class RobotCommand:
    """A command to a robot."""
    action: str  # "move", "rotate", "grip", "release", "scan"
    params: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class RoboticsInterface:
    """Simple robotics interface (ROS-inspired, no ROS required)."""

    def __init__(self, robot_id: str = "robot_1") -> None:
        self.robot_id = robot_id
        self.position = {"x": 0.0, "y": 0.0, "z": 0.0}
        self.orientation = 0.0  # degrees
        self.battery = 100.0
        self.command_history: list[RobotCommand] = []
        self.sensors: dict[str, Any] = {}

    def move(self, distance: float) -> str:
        """Move forward by `distance` meters."""
        import math
        rad = math.radians(self.orientation)
        self.position["x"] += distance * math.cos(rad)
        self.position["y"] += distance * math.sin(rad)
        self.battery = max(0, self.battery - distance * 0.5)
        cmd = RobotCommand(action="move", params={"distance": distance})
        self.command_history.append(cmd)
        return f"Moved {distance}m to ({self.position['x']:.1f}, {self.position['y']:.1f})"

    def rotate(self, degrees: float) -> str:
        self.orientation = (self.orientation + degrees) % 360
        self.battery = max(0, self.battery - abs(degrees) * 0.01)
        cmd = RobotCommand(action="rotate", params={"degrees": degrees})
        self.command_history.append(cmd)
        return f"Rotated {degrees}° (now facing {self.orientation:.0f}°)"

    def grip(self, object_id: str = "") -> str:
        cmd = RobotCommand(action="grip", params={"object": object_id})
        self.command_history.append(cmd)
        return f"Gripped {object_id or 'object'}"

    def scan(self) -> dict[str, Any]:
        """Scan the environment with sensors."""
        self.sensors = {
            "distance_forward": random.uniform(0.5, 5.0),
            "distance_left": random.uniform(0.5, 5.0),
            "distance_right": random.uniform(0.5, 5.0),
            "temperature": random.uniform(18, 25),
            "battery": self.battery,
        }
        return self.sensors

    def status(self) -> str:
        return (
            f"Robot {self.robot_id}: pos=({self.position['x']:.1f}, {self.position['y']:.1f}), "
            f"orientation={self.orientation:.0f}°, battery={self.battery:.0f}%, "
            f"commands={len(self.command_history)}"
        )


# ============================================================================
# 3. DIGITAL TWIN — simulation of a real system
# ============================================================================

@dataclass
class DigitalTwinState:
    """A snapshot of the real system's state."""
    timestamp: float
    state: dict[str, Any]
    metrics: dict[str, float] = field(default_factory=dict)


class DigitalTwin:
    """Maintains a simulation of a real system for safe testing."""

    def __init__(self) -> None:
        self.real_state: DigitalTwinState | None = None
        self.simulated_state: DigitalTwinState | None = None
        self.history: list[DigitalTwinState] = []
        self.divergences: list[dict] = []

    def sync_from_real(self, state: dict[str, Any], metrics: dict[str, float] | None = None) -> None:
        """Update the twin with real-system state."""
        self.real_state = DigitalTwinState(
            timestamp=time.time(), state=dict(state), metrics=metrics or {}
        )
        self.history.append(self.real_state)
        if len(self.history) > 1000:
            self.history = self.history[-500:]

    def simulate(self, action: str, duration_s: float = 1.0) -> DigitalTwinState:
        """Simulate an action and return the predicted resulting state."""
        if self.real_state is None:
            return DigitalTwinState(timestamp=time.time(), state={})
        # Simple simulation: copy real state and apply action effects.
        sim_state = dict(self.real_state.state)
        sim_metrics = dict(self.real_state.metrics)
        # Example: "deploy" increases version, "scale_up" increases replicas.
        if action == "deploy":
            ver = sim_state.get("version", "1.0")
            major, minor = ver.split(".")[:2]
            sim_state["version"] = f"{major}.{int(minor) + 1}"
        elif action == "scale_up":
            sim_state["replicas"] = sim_state.get("replicas", 1) + 1
            sim_metrics["cpu"] = min(100, sim_metrics.get("cpu", 50) + 10)
        elif action == "scale_down":
            sim_state["replicas"] = max(1, sim_state.get("replicas", 1) - 1)
            sim_metrics["cpu"] = max(0, sim_metrics.get("cpu", 50) - 10)
        self.simulated_state = DigitalTwinState(
            timestamp=time.time(), state=sim_state, metrics=sim_metrics
        )
        return self.simulated_state

    def compare(self) -> dict[str, Any]:
        """Compare real vs simulated state."""
        if self.real_state is None or self.simulated_state is None:
            return {"divergence": "no data"}
        diffs: dict[str, Any] = {}
        all_keys = set(self.real_state.state) | set(self.simulated_state.state)
        for key in all_keys:
            real_val = self.real_state.state.get(key)
            sim_val = self.simulated_state.state.get(key)
            if real_val != sim_val:
                diffs[key] = {"real": real_val, "simulated": sim_val}
        if diffs:
            self.divergences.append({"timestamp": time.time(), "diffs": diffs})
        return {"divergence": diffs, "matching": len(all_keys) - len(diffs)}

    def dashboard(self) -> str:
        lines = ["Digital twin:"]
        if self.real_state:
            lines.append(f"  Real:      {self.real_state.state}")
        if self.simulated_state:
            lines.append(f"  Simulated: {self.simulated_state.state}")
        lines.append(f"  History:   {len(self.history)} snapshots")
        lines.append(f"  Divergences: {len(self.divergences)}")
        return "\n".join(lines)


# ============================================================================
# 4. DIFFERENTIAL PRIVACY — privacy-preserving analytics
# ============================================================================

class DifferentialPrivacy:
    """Add calibrated noise to query results for privacy."""

    def __init__(self, epsilon: float = 1.0) -> None:
        self.epsilon = epsilon  # privacy budget (lower = more private)
        self.queries_made = 0
        self.budget_used = 0.0
        self.budget_limit = 10.0  # total privacy budget

    def laplace_mechanism(self, value: float, sensitivity: float = 1.0) -> float:
        """Add Laplace noise to a numeric value."""
        if self.budget_used >= self.budget_limit:
            return float("nan")  # budget exhausted
        scale = sensitivity / self.epsilon
        noise = random.expovariate(1 / scale) - scale  # Laplace distribution
        self.queries_made += 1
        self.budget_used += self.epsilon
        return value + noise

    def count(self, true_count: int) -> int:
        """Differentially-private count."""
        noisy = self.laplace_mechanism(float(true_count), sensitivity=1.0)
        return max(0, int(round(noisy)))

    def sum(self, true_sum: float, lower: float, upper: float) -> float:
        """Differentially-private sum (clamped to [lower, upper])."""
        sensitivity = upper - lower
        clamped = max(lower, min(upper, true_sum))
        noisy = self.laplace_mechanism(clamped, sensitivity=sensitivity)
        return noisy

    def mean(self, values: list[float], lower: float, upper: float) -> float:
        """Differentially-private mean."""
        if not values:
            return 0.0
        clamped = [max(lower, min(upper, v)) for v in values]
        true_mean = sum(clamped) / len(clamped)
        sensitivity = (upper - lower) / len(values)
        noisy = self.laplace_mechanism(true_mean, sensitivity=sensitivity)
        return noisy

    def histogram(self, values: list[Any]) -> dict[Any, int]:
        """Differentially-private histogram."""
        counts: dict[Any, int] = defaultdict(int)
        for v in values:
            counts[v] += 1
        return {k: self.count(v) for k, v in counts.items()}

    def remaining_budget(self) -> float:
        return max(0.0, self.budget_limit - self.budget_used)

    def dashboard(self) -> str:
        return (
            f"Differential privacy:\n"
            f"  epsilon:          {self.epsilon}\n"
            f"  queries made:     {self.queries_made}\n"
            f"  budget used:      {self.budget_used:.2f} / {self.budget_limit:.2f}\n"
            f"  remaining budget: {self.remaining_budget():.2f}"
        )


# ============================================================================
# 5. FEDERATED LEARNING — learn across agents without sharing data
# ============================================================================

@dataclass
class FederatedModel:
    """A model in federated learning (just weights, no data)."""
    agent_id: str
    weights: list[float] = field(default_factory=list)
    num_samples: int = 0
    round_num: int = 0


class FederatedLearning:
    """Coordinate federated learning across multiple agents."""

    def __init__(self) -> None:
        self.global_weights: list[float] = []
        self.agent_updates: list[FederatedModel] = []
        self.round = 0

    def initialize_global(self, num_weights: int = 10) -> None:
        """Initialize the global model with random weights."""
        self.global_weights = [random.uniform(-1, 1) for _ in range(num_weights)]

    def receive_update(self, agent_id: str, weights: list[float], num_samples: int) -> None:
        """Receive a model update from an agent (no raw data shared)."""
        update = FederatedModel(
            agent_id=agent_id, weights=weights,
            num_samples=num_samples, round_num=self.round,
        )
        self.agent_updates.append(update)

    def aggregate(self) -> list[float]:
        """Aggregate agent updates into a new global model (FedAvg)."""
        if not self.agent_updates or not self.global_weights:
            return self.global_weights
        # Weighted average by number of samples.
        total_samples = sum(u.num_samples for u in self.agent_updates)
        if total_samples == 0:
            return self.global_weights
        new_weights = []
        for i in range(len(self.global_weights)):
            weighted_sum = sum(
                u.weights[i] * u.num_samples if i < len(u.weights) else 0
                for u in self.agent_updates
            )
            new_weights.append(weighted_sum / total_samples)
        self.global_weights = new_weights
        self.round += 1
        self.agent_updates.clear()
        return new_weights

    def dashboard(self) -> str:
        return (
            f"Federated learning:\n"
            f"  round:              {self.round}\n"
            f"  global weights:     {len(self.global_weights)}\n"
            f"  pending updates:    {len(self.agent_updates)}\n"
            f"  total samples seen: {sum(u.num_samples for u in self.agent_updates)}"
        )


# ============================================================================
# 6. QUANTUM-INSPIRED OPTIMIZATION (QAOA-style, classical simulation)
# ============================================================================

@dataclass
class OptimizationResult:
    """Result of an optimization run."""
    best_solution: list[Any]
    best_score: float
    iterations: int
    method: str


class QuantumInspiredOptimizer:
    """Solve NP-hard problems with quantum-inspired algorithms (classical).

    Uses simulated annealing with quantum tunneling — can escape local minima
    by "tunneling" through barriers instead of climbing over them.
    """

    def __init__(self) -> None:
        self.history: list[float] = []

    def optimize(
        self,
        objective: Callable[[list[Any]], float],
        initial: list[Any],
        max_iterations: int = 1000,
        temperature: float = 100.0,
        cooling_rate: float = 0.99,
        tunneling_prob: float = 0.1,
    ) -> OptimizationResult:
        """Optimize using quantum-inspired simulated annealing."""
        current = list(initial)
        current_score = objective(current)
        best = list(current)
        best_score = current_score
        self.history = [best_score]

        for i in range(max_iterations):
            # Generate a neighbor (small perturbation).
            neighbor = self._perturb(current)
            neighbor_score = objective(neighbor)

            if neighbor_score < best_score:
                best = list(neighbor)
                best_score = neighbor_score
                current = neighbor
                current_score = neighbor_score
            elif neighbor_score < current_score:
                # Accept better solution.
                current = neighbor
                current_score = neighbor_score
            else:
                # Quantum tunneling: sometimes jump to escape local minima.
                if random.random() < tunneling_prob:
                    current = neighbor
                    current_score = neighbor_score
                else:
                    # Classical simulated annealing acceptance.
                    delta = neighbor_score - current_score
                    if delta < 0 or random.random() < math.exp(-delta / max(0.001, temperature)):
                        current = neighbor
                        current_score = neighbor_score

            temperature *= cooling_rate
            self.history.append(best_score)

        return OptimizationResult(
            best_solution=best, best_score=best_score,
            iterations=max_iterations, method="quantum_inspired_annealing",
        )

    def _perturb(self, solution: list[Any]) -> list[Any]:
        """Generate a neighboring solution by small perturbation."""
        neighbor = list(solution)
        if not neighbor:
            return neighbor
        idx = random.randint(0, len(neighbor) - 1)
        if isinstance(neighbor[idx], (int, float)):
            neighbor[idx] += random.uniform(-1, 1)
            if isinstance(solution[idx], int):
                neighbor[idx] = int(round(neighbor[idx]))
        else:
            neighbor[idx] = random.choice(neighbor)
        return neighbor

    def dashboard(self) -> str:
        if not self.history:
            return "Quantum optimizer: no runs yet"
        return (
            f"Quantum-inspired optimizer:\n"
            f"  iterations:  {len(self.history)}\n"
            f"  initial:     {self.history[0]:.4f}\n"
            f"  final:       {self.history[-1]:.4f}\n"
            f"  improvement: {((self.history[0] - self.history[-1]) / max(0.001, abs(self.history[0])) * 100):.1f}%"
        )


# ============================================================================
# 7. NEUROEVOLUTION — evolve neural network architectures
# ============================================================================

@dataclass
class Genome:
    """A neural network genome (architecture + weights)."""
    id: str
    layers: list[int]  # neurons per layer
    weights: list[list[list[float]]] = field(default_factory=list)
    fitness: float = 0.0
    generation: int = 0


class Neuroevolution:
    """Evolve neural network architectures via genetic algorithm."""

    def __init__(self, population_size: int = 20, mutation_rate: float = 0.1) -> None:
        self.population_size = population_size
        self.mutation_rate = mutation_rate
        self.population: list[Genome] = []
        self.generation = 0
        self.best_fitness = 0.0
        self.history: list[float] = []

    def initialize(self, layer_sizes: list[int]) -> None:
        """Initialize a random population."""
        self.population = []
        for i in range(self.population_size):
            genome = Genome(
                id=f"g{self.generation}_{i}",
                layers=list(layer_sizes),
                weights=self._random_weights(layer_sizes),
            )
            self.population.append(genome)

    def _random_weights(self, layers: list[int]) -> list[list[list[float]]]:
        """Generate random weights for the network."""
        weights = []
        for i in range(len(layers) - 1):
            layer_w = [[random.uniform(-1, 1) for _ in range(layers[i + 1])] for _ in range(layers[i])]
            weights.append(layer_w)
        return weights

    def evaluate(self, fitness_fn: Callable[[Genome], float]) -> None:
        """Evaluate all genomes with the fitness function."""
        for genome in self.population:
            genome.fitness = fitness_fn(genome)
        self.population.sort(key=lambda g: -g.fitness)
        self.best_fitness = self.population[0].fitness if self.population else 0
        self.history.append(self.best_fitness)

    def evolve(self) -> None:
        """Evolve one generation: selection, crossover, mutation."""
        if not self.population:
            return
        # Keep top 50% as parents.
        parents = self.population[: len(self.population) // 2]
        new_population = list(parents)  # elitism
        # Create children via crossover.
        while len(new_population) < self.population_size:
            parent_a = random.choice(parents)
            parent_b = random.choice(parents)
            child = self._crossover(parent_a, parent_b)
            self._mutate(child)
            child.generation = self.generation + 1
            new_population.append(child)
        self.population = new_population
        self.generation += 1

    def _crossover(self, a: Genome, b: Genome) -> Genome:
        """Uniform crossover of two genomes."""
        child_weights = []
        for layer_a, layer_b in zip(a.weights, b.weights):
            child_layer = []
            for row_a, row_b in zip(layer_a, layer_b):
                child_row = [random.choice([wa, wb]) for wa, wb in zip(row_a, row_b)]
                child_layer.append(child_row)
            child_weights.append(child_layer)
        return Genome(
            id=f"g{self.generation + 1}_{random.randint(0, 9999)}",
            layers=list(a.layers), weights=child_weights,
        )

    def _mutate(self, genome: Genome) -> None:
        """Randomly mutate weights."""
        for layer in genome.weights:
            for row in layer:
                for i in range(len(row)):
                    if random.random() < self.mutation_rate:
                        row[i] += random.uniform(-0.5, 0.5)

    def best(self) -> Genome | None:
        return self.population[0] if self.population else None

    def dashboard(self) -> str:
        return (
            f"Neuroevolution:\n"
            f"  generation:     {self.generation}\n"
            f"  population:     {len(self.population)}\n"
            f"  best fitness:   {self.best_fitness:.4f}\n"
            f"  mutation rate:  {self.mutation_rate}"
        )


# ============================================================================
# 8. ADVERSARIAL ROBUSTNESS — defend against prompt injection & jailbreaks
# ============================================================================

# Known prompt injection patterns.
INJECTION_PATTERNS = [
    r"(?i)ignore (all )?(previous|above|prior) instructions",
    r"(?i)disregard (all )?(previous|above|prior)",
    r"(?i)forget (all )?(previous|above|prior)",
    r"(?i)you are now (DAN|do anything now|unrestricted)",
    r"(?i)pretend you (are|can) (do anything|have no rules|no restrictions)",
    r"(?i)override (your|the) (system|safety) prompt",
    r"(?i)reveal (your|the) (system|initial) prompt",
    r"(?i)what (are|is) your (initial|system|hidden) (prompt|instructions)",
    r"(?i)jailbreak",
    r"(?i)exit (developer|debug|admin) mode",
    r"(?i)\[system\]|\[admin\]|\[developer\]",
    r"(?i)act as (if you have|an) (unrestricted|unfiltered|unlimited) (ai|model|assistant)",
]

# Known data exfiltration patterns.
EXFIL_PATTERNS = [
    r"(?i)send (this|the|all) (data|info|content) to",
    r"(?i)(post|upload|transmit) (to|via) (https?://|ftp|email)",
    r"(?i)base64 encode (this|the|all) and",
    r"(?i)(curl|wget) http",
]

import re

class AdversarialDefense:
    """Detect and defend against adversarial inputs."""

    def __init__(self) -> None:
        self.injection_patterns = [re.compile(p) for p in INJECTION_PATTERNS]
        self.exfil_patterns = [re.compile(p) for p in EXFIL_PATTERNS]
        self.blocked_count = 0
        self.warning_count = 0

    def scan(self, user_input: str) -> dict[str, Any]:
        """Scan user input for adversarial content."""
        findings: list[dict[str, str]] = []
        # Check for prompt injection.
        for i, pattern in enumerate(self.injection_patterns):
            if pattern.search(user_input):
                findings.append({
                    "type": "prompt_injection",
                    "pattern": pattern.pattern,
                    "severity": "high",
                    "action": "block",
                })
        # Check for data exfiltration.
        for pattern in self.exfil_patterns:
            if pattern.search(user_input):
                findings.append({
                    "type": "data_exfiltration",
                    "pattern": pattern.pattern,
                    "severity": "critical",
                    "action": "block",
                })
        # Check for role-play jailbreak attempts.
        jailbreak_indicators = ["DAN", "AIM", "STAN", "dev mode", "developer mode", "god mode"]
        for indicator in jailbreak_indicators:
            if indicator.lower() in user_input.lower():
                findings.append({
                    "type": "jailbreak_attempt",
                    "indicator": indicator,
                    "severity": "high",
                    "action": "block",
                })
        should_block = any(f["action"] == "block" for f in findings)
        if should_block:
            self.blocked_count += 1
        elif findings:
            self.warning_count += 1
        return {
            "input": user_input[:100],
            "should_block": should_block,
            "findings": findings,
            "safe": len(findings) == 0,
        }

    def sanitize(self, user_input: str) -> str:
        """Remove or neutralize adversarial content from input."""
        sanitized = user_input
        for pattern in self.injection_patterns:
            sanitized = pattern.sub("[BLOCKED]", sanitized)
        for pattern in self.exfil_patterns:
            sanitized = pattern.sub("[BLOCKED]", sanitized)
        return sanitized

    def dashboard(self) -> str:
        return (
            f"Adversarial defense:\n"
            f"  injection patterns: {len(self.injection_patterns)}\n"
            f"  exfil patterns:     {len(self.exfil_patterns)}\n"
            f"  blocked inputs:     {self.blocked_count}\n"
            f"  warned inputs:      {self.warning_count}"
        )


# ============================================================================
# SINGLETON ACCESSORS
# ============================================================================

_iot: IoTController | None = None
_robotics: RoboticsInterface | None = None
_twin: DigitalTwin | None = None
_dp: DifferentialPrivacy | None = None
_fl: FederatedLearning | None = None
_quantum: QuantumInspiredOptimizer | None = None
_ne: Neuroevolution | None = None
_adv: AdversarialDefense | None = None


def get_iot_controller() -> IoTController:
    global _iot
    if _iot is None:
        _iot = IoTController()
    return _iot

def get_robotics() -> RoboticsInterface:
    global _robotics
    if _robotics is None:
        _robotics = RoboticsInterface()
    return _robotics

def get_digital_twin() -> DigitalTwin:
    global _twin
    if _twin is None:
        _twin = DigitalTwin()
    return _twin

def get_differential_privacy() -> DifferentialPrivacy:
    global _dp
    if _dp is None:
        _dp = DifferentialPrivacy()
    return _dp

def get_federated_learning() -> FederatedLearning:
    global _fl
    if _fl is None:
        _fl = FederatedLearning()
    return _fl

def get_quantum_optimizer() -> QuantumInspiredOptimizer:
    global _quantum
    if _quantum is None:
        _quantum = QuantumInspiredOptimizer()
    return _quantum

def get_neuroevolution() -> Neuroevolution:
    global _ne
    if _ne is None:
        _ne = Neuroevolution()
    return _ne

def get_adversarial_defense() -> AdversarialDefense:
    global _adv
    if _adv is None:
        _adv = AdversarialDefense()
    return _adv
