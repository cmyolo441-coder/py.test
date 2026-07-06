"""Multi-agent economy — agents with budgets, trading, and negotiation.

Agents have wallets (token/USD budgets), can offer services, bid on tasks,
and negotiate prices. This creates a market-based allocation of work.

Components:
  * AgentWallet — tracks each agent's budget and spending
  * ServiceOffer — an agent advertises a service with a price
  * TaskBid — agents bid on tasks (price, quality, ETA)
  * Auction — resolves bids to pick the winner (Vickrey auction)
  * Negotiation — alternating-offers protocol for price agreement
  * Coalition — agents form teams for complex tasks

Inspired by: computational economics, mechanism design, auction theory.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .logging_config import get_logger

log = get_logger("agent.economy")


@dataclass
class AgentWallet:
    """An agent's budget and transaction history."""
    agent_id: str
    balance_tokens: float = 100_000.0  # starting budget in tokens
    balance_usd: float = 1.0  # starting budget in USD
    spent_tokens: float = 0.0
    spent_usd: float = 0.0
    earned_tokens: float = 0.0
    earned_usd: float = 0.0
    transactions: list[dict] = field(default_factory=list)

    def can_afford(self, cost_tokens: float = 0, cost_usd: float = 0) -> bool:
        return self.balance_tokens >= cost_tokens and self.balance_usd >= cost_usd

    def debit(self, amount_tokens: float = 0, amount_usd: float = 0, reason: str = "") -> bool:
        if not self.can_afford(amount_tokens, amount_usd):
            return False
        self.balance_tokens -= amount_tokens
        self.balance_usd -= amount_usd
        self.spent_tokens += amount_tokens
        self.spent_usd += amount_usd
        self.transactions.append({
            "type": "debit", "tokens": amount_tokens, "usd": amount_usd,
            "reason": reason, "timestamp": time.time(),
        })
        return True

    def credit(self, amount_tokens: float = 0, amount_usd: float = 0, reason: str = "") -> None:
        self.balance_tokens += amount_tokens
        self.balance_usd += amount_usd
        self.earned_tokens += amount_tokens
        self.earned_usd += amount_usd
        self.transactions.append({
            "type": "credit", "tokens": amount_tokens, "usd": amount_usd,
            "reason": reason, "timestamp": time.time(),
        })


@dataclass
class ServiceOffer:
    """An agent offers a service at a price."""
    offer_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    agent_id: str = ""
    service_name: str = ""  # e.g., "code_review"
    description: str = ""
    price_tokens: float = 0
    price_usd: float = 0
    quality_score: float = 0.5  # 0-1, historical quality
    avg_latency_s: float = 60.0
    available: bool = True


@dataclass
class TaskBid:
    """An agent bids on a task."""
    bid_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    agent_id: str = ""
    task_description: str = ""
    bid_price_tokens: float = 0
    bid_price_usd: float = 0
    estimated_time_s: float = 60.0
    quality_estimate: float = 0.5
    reputation: float = 0.5


@dataclass
class AuctionResult:
    task: str
    winner_id: str
    winning_bid: TaskBid
    price_paid: float  # in Vickrey auction, this is the second-highest bid
    auction_type: str = "vickrey"


@dataclass
class Coalition:
    """A group of agents working together on a task."""
    coalition_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    members: list[str] = field(default_factory=list)
    task: str = ""
    reward_tokens: float = 0
    reward_usd: float = 0
    contribution_split: dict[str, float] = field(default_factory=dict)  # agent_id -> fraction


class AgentEconomy:
    """Manages the multi-agent economy."""

    def __init__(self) -> None:
        self.wallets: dict[str, AgentWallet] = {}
        self.offers: list[ServiceOffer] = []
        self.reputations: dict[str, float] = {}  # agent_id -> reputation (0-1)

    def register_agent(self, agent_id: str, starting_tokens: float = 100_000, starting_usd: float = 1.0) -> AgentWallet:
        wallet = AgentWallet(agent_id=agent_id, balance_tokens=starting_tokens, balance_usd=starting_usd)
        self.wallets[agent_id] = wallet
        self.reputations[agent_id] = 0.5  # neutral start
        return wallet

    def offer_service(self, agent_id: str, service_name: str, price_tokens: float, price_usd: float = 0, quality: float = 0.5) -> ServiceOffer:
        offer = ServiceOffer(
            agent_id=agent_id, service_name=service_name,
            price_tokens=price_tokens, price_usd=price_usd,
            quality_score=quality, available=True,
        )
        self.offers.append(offer)
        return offer

    def find_offers(self, service_name: str, max_price_tokens: float | None = None) -> list[ServiceOffer]:
        results = [o for o in self.offers if o.service_name == service_name and o.available]
        if max_price_tokens is not None:
            results = [o for o in results if o.price_tokens <= max_price_tokens]
        # Sort by price * (1/quality) — best value first.
        results.sort(key=lambda o: o.price_tokens / max(0.1, o.quality_score))
        return results

    def buy_service(self, buyer_id: str, offer: ServiceOffer) -> bool:
        """Buy a service. Transfers payment from buyer to seller."""
        buyer = self.wallets.get(buyer_id)
        seller = self.wallets.get(offer.agent_id)
        if buyer is None or seller is None:
            return False
        if not buyer.can_afford(offer.price_tokens, offer.price_usd):
            return False
        buyer.debit(offer.price_tokens, offer.price_usd, f"bought {offer.service_name} from {offer.agent_id}")
        seller.credit(offer.price_tokens, offer.price_usd, f"sold {offer.service_name} to {buyer_id}")
        log.info("Trade: %s bought %s from %s for %s tokens", buyer_id, offer.service_name, offer.agent_id, offer.price_tokens)
        return True

    def run_vickrey_auction(self, task: str, bids: list[TaskBid]) -> AuctionResult:
        """Run a Vickrey auction (second-price sealed-bid).

        Winner pays the second-lowest bid price, not their own.
        """
        if not bids:
            raise ValueError("No bids to auction")
        # Sort by bid price (lowest wins, but pays second-lowest).
        sorted_bids = sorted(bids, key=lambda b: b.bid_price_tokens)
        winner = sorted_bids[0]
        # Price paid = second-lowest bid (or winner's bid if only one).
        if len(sorted_bids) > 1:
            price_paid = sorted_bids[1].bid_price_tokens
        else:
            price_paid = winner.bid_price_tokens
        return AuctionResult(
            task=task, winner_id=winner.agent_id, winning_bid=winner,
            price_paid=price_paid, auction_type="vickrey",
        )

    def negotiate(self, agent_a: str, agent_b: str, task: str, initial_price: float, rounds: int = 5) -> tuple[bool, float]:
        """Alternating-offers negotiation.

        Agent A starts high, Agent B counters low. They converge.
        Returns (agreed, final_price).
        """
        price = initial_price
        for round_num in range(rounds):
            # Agent A's offer (seller — wants higher price).
            offer_a = price * (1.0 + 0.1 * (1 - round_num / rounds))
            # Agent B's counter (buyer — wants lower price).
            counter_b = offer_a * (0.8 + 0.05 * round_num)
            # Check if they agree (within 5%).
            if abs(offer_a - counter_b) / max(offer_a, counter_b) < 0.05:
                agreed_price = (offer_a + counter_b) / 2
                log.info("Negotiation agreed: %s and %s on '%s' for %.2f tokens", agent_a, agent_b, task, agreed_price)
                return True, agreed_price
            price = counter_b
        log.info("Negotiation failed: %s and %s on '%s'", agent_a, agent_b, task)
        return False, price

    def form_coalition(self, task: str, member_ids: list[str], reward_tokens: float) -> Coalition:
        """Form a coalition of agents to tackle a complex task."""
        # Split reward by reputation.
        total_rep = sum(self.reputations.get(mid, 0.5) for mid in member_ids)
        split = {mid: self.reputations.get(mid, 0.5) / total_rep for mid in member_ids}
        coalition = Coalition(
            members=member_ids, task=task, reward_tokens=reward_tokens,
            contribution_split=split,
        )
        return coalition

    def distribute_reward(self, coalition: Coalition) -> None:
        """Distribute the coalition's reward to members."""
        for member_id, fraction in coalition.contribution_split.items():
            wallet = self.wallets.get(member_id)
            if wallet:
                wallet.credit(amount_tokens=coalition.reward_tokens * fraction, reason=f"coalition reward for {coalition.task}")

    def update_reputation(self, agent_id: str, success: bool, delta: float = 0.05) -> None:
        """Update an agent's reputation based on task outcome."""
        current = self.reputations.get(agent_id, 0.5)
        if success:
            current = min(1.0, current + delta)
        else:
            current = max(0.0, current - delta * 2)
        self.reputations[agent_id] = current

    def leaderboard(self) -> str:
        lines = ["Agent economy leaderboard:"]
        # Sort by reputation.
        sorted_agents = sorted(self.reputations.items(), key=lambda x: -x[1])
        for agent_id, rep in sorted_agents:
            wallet = self.wallets.get(agent_id)
            balance = f"{wallet.balance_tokens:.0f} tok, ${wallet.balance_usd:.2f}" if wallet else "no wallet"
            lines.append(f"  {agent_id:<20} rep={rep:.0%}  balance={balance}")
        lines.append(f"\nActive offers: {len(self.offers)}")
        return "\n".join(lines)


_economy: AgentEconomy | None = None


def get_economy() -> AgentEconomy:
    global _economy
    if _economy is None:
        _economy = AgentEconomy()
    return _economy
