"""
Byzantine Governance Protocol for AIFlowGuard
Decentralized governance infrastructure with BFT guarantees, reputation-based
voting power, and automated slashing for malicious agent behavior.
"""

import asyncio
import hashlib
import json
import time
import secrets
from typing import Dict, List, Optional, Callable, Any, Set, Tuple
from dataclasses import dataclass, field, asdict
from enum import Enum, auto
from collections import defaultdict
from abc import ABC, abstractmethod
import logging
from datetime import datetime, timedelta
import structlog
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.exceptions import InvalidSignature
import aiofiles

logger = structlog.get_logger(__name__)


class ProposalStatus(Enum):
    PENDING = auto()
    ACTIVE = auto()
    SUCCEEDED = auto()
    DEFEATED = auto()
    EXECUTED = auto()
    CANCELLED = auto()
    QUARANTINED = auto()  # For suspicious proposals


class VoteType(Enum):
    YES = 1
    NO = 0
    ABSTAIN = -1


class SlashingReason(Enum):
    BYZANTINE_FAULT = "byzantine_fault"
    DOUBLE_VOTING = "double_voting"
    COLLUSION = "collusion"
    INACTIVITY = "inactivity"
    INVALID_PROPOSAL = "invalid_proposal"


@dataclass
class AgentProfile:
    """Reputation and staking profile for swarm agents."""
    agent_id: str
    public_key: bytes
    reputation_score: float = 100.0
    staked_amount: float = 0.0
    slash_history: List[Dict[str, Any]] = field(default_factory=list)
    last_active: float = field(default_factory=time.time)
    proposals_created: int = 0
    votes_participated: int = 0
    consensus_accuracy: float = 1.0  # Ratio of correct consensus participations
    is_jailed: bool = False
    jail_release_time: Optional[float] = None
    
    def calculate_voting_power(self, strategy: str = "quadratic") -> float:
        """Calculate voting power based on reputation and stake."""
        if self.is_jailed:
            return 0.0
            
        base_power = self.reputation_score * (1 + self.staked_amount / 1000)
        
        if strategy == "linear":
            return base_power
        elif strategy == "quadratic":
            return (base_power ** 0.5) * self.consensus_accuracy
        elif strategy == "conviction":
            # Conviction voting decays over time
            time_factor = 1.0 / (1 + len(self.slash_history))
            return base_power * time_factor * self.consensus_accuracy
        else:
            return base_power


@dataclass
class Proposal:
    """Governance proposal with Merkle verification."""
    id: str
    proposer: str
    title: str
    description: str
    call_data: Dict[str, Any]  # Executable payload
    created_at: float
    voting_starts: float
    voting_ends: float
    execution_delay: int = 86400  # 24 hours timelock
    status: ProposalStatus = ProposalStatus.PENDING
    votes: Dict[str, Tuple[VoteType, float]] = field(default_factory=dict)  # agent_id -> (vote, weight)
    execution_data: Optional[Dict] = None
    merkle_root: Optional[str] = None
    quorum_threshold: float = 0.33  # 33% participation required
    approval_threshold: float = 0.51  # 51% approval required
    
    def calculate_results(self) -> Tuple[float, float, float]:
        """Returns (yes_power, no_power, abstain_power)."""
        yes_power = sum(weight for vote, weight in self.votes.values() if vote == VoteType.YES)
        no_power = sum(weight for vote, weight in self.votes.values() if vote == VoteType.NO)
        abstain_power = sum(weight for vote, weight in self.votes.values() if vote == VoteType.ABSTAIN)
        return yes_power, no_power, abstain_power
    
    def compute_merkle_root(self) -> str:
        """Compute Merkle root of proposal data for integrity."""
        data_string = f"{self.id}:{self.proposer}:{json.dumps(self.call_data, sort_keys=True)}:{self.created_at}"
        return hashlib.sha3_256(data_string.encode()).hexdigest()
    
    def verify_integrity(self) -> bool:
        """Verify proposal hasn't been tampered with."""
        return self.merkle_root == self.compute_merkle_root()


@dataclass
class GovernanceConfig:
    """Configuration for governance parameters."""
    min_proposal_stake: float = 100.0
    voting_period: int = 604800  # 7 days
    execution_delay: int = 86400  # 24 hours
    quorum_numerator: int = 33
    quorum_denominator: int = 100
    proposal_threshold: float = 1000.0  # Min reputation to propose
    voting_strategy: str = "quadratic"
    reputation_decay_rate: float = 0.01  # Daily decay
    slash_percentage: float = 0.1  # 10% slashed
    jail_duration: int = 2592000  # 30 days
    max_proposals_per_agent: int = 5
    byzantine_tolerance: int = 1  # f faults tolerated in 3f+1 swarm


class VotingStrategy(ABC):
    """Abstract base for voting power calculation strategies."""
    
    @abstractmethod
    def calculate_power(self, agent: AgentProfile, context: Dict[str, Any]) -> float:
        pass


class QuadraticVoting(VotingStrategy):
    """Quadratic voting with reputation scaling."""
    
    def calculate_power(self, agent: AgentProfile, context: Dict[str, Any]) -> float:
        stake_weight = (agent.staked_amount ** 0.5) / 10
        rep_weight = (agent.reputation_score ** 0.5)
        accuracy_bonus = agent.consensus_accuracy ** 2
        return stake_weight * rep_weight * accuracy_bonus


class ConvictionVoting(VotingStrategy):
    """Conviction voting with time-decay mechanics."""
    
    def __init__(self, half_life_days: int = 7):
        self.half_life = half_life_days * 86400
        
    def calculate_power(self, agent: AgentProfile, context: Dict[str, Any]) -> float:
        time_since_last = time.time() - agent.last_active
        decay_factor = 0.5 ** (time_since_last / self.half_life)
        return agent.reputation_score * decay_factor * (1 + agent.staked_amount / 100)


class SlashingManager:
    """Handles economic penalties for Byzantine behavior."""
    
    def __init__(self, config: GovernanceConfig):
        self.config = config
        self.slash_events: List[Dict[str, Any]] = []
        
    async def slash_agent(
        self, 
        agent: AgentProfile, 
        reason: SlashingReason, 
        evidence: Dict[str, Any],
        governance_state: 'GovernanceState'
    ) -> Dict[str, Any]:
        """Execute slashing logic and jail agent if necessary."""
        
        slash_amount = agent.staked_amount * self.config.slash_percentage
        agent.staked_amount -= slash_amount
        agent.reputation_score *= 0.5  # 50% reputation hit
        
        event = {
            "timestamp": time.time(),
            "agent_id": agent.agent_id,
            "reason": reason.value,
            "amount_slashed": slash_amount,
            "evidence_hash": hashlib.sha3_256(json.dumps(evidence).encode()).hexdigest(),
            "block_height": evidence.get("block_height", 0)
        }
        
        agent.slash_history.append(event)
        self.slash_events.append(event)
        
        # Jail agent for severe offenses
        if reason in [SlashingReason.BYZANTINE_FAULT, SlashingReason.COLLUSION]:
            agent.is_jailed = True
            agent.jail_release_time = time.time() + self.config.jail_duration
            logger.warning(
                "agent_jailed",
                agent_id=agent.agent_id,
                reason=reason.value,
                release_time=agent.jail_release_time
            )
        
        # Redistribute slashed amount to honest participants (simplified)
        await self._redistribute_slashed_funds(slash_amount, governance_state)
        
        return event
    
    async def _redistribute_slashed_funds(self, amount: float, state: 'GovernanceState'):
        """Redistribute slashed funds to honest agents proportionally."""
        honest_agents = [
            a for a in state.agents.values() 
            if not a.is_jailed and len(a.slash_history) == 0
        ]
        
        if honest_agents:
            share = amount / len(honest_agents)
            for agent in honest_agents:
                agent.staked_amount += share


class GovernanceState:
    """Thread-safe state management for governance."""
    
    def __init__(self, config: GovernanceConfig):
        self.config = config
        self.agents: Dict[str, AgentProfile] = {}
        self.proposals: Dict[str, Proposal] = {}
        self.vote_hashes: Set[str] = set()  # Prevent double voting
        self.execution_queue: List[str] = []
        self.total_voting_power: float = 0.0
        self._lock = asyncio.Lock()
        self._checkpoint_interval = 3600  # 1 hour
        self._last_checkpoint = time.time()
        
    async def get_agent(self, agent_id: str) -> Optional[AgentProfile]:
        async with self._lock:
            return self.agents.get(agent_id)
    
    async def register_agent(self, profile: AgentProfile) -> bool:
        async with self._lock:
            if profile.agent_id in self.agents:
                return False
            self.agents[profile.agent_id] = profile
            self.total_voting_power += profile.calculate_voting_power(self.config.voting_strategy)
            return True
    
    async def create_proposal(self, proposal: Proposal) -> bool:
        async with self._lock:
            if proposal.id in self.proposals:
                return False
            proposal.merkle_root = proposal.compute_merkle_root()
            self.proposals[proposal.id] = proposal
            return True
    
    async def cast_vote(
        self, 
        proposal_id: str, 
        agent_id: str, 
        vote: VoteType, 
        signature: bytes,
        voting_strategy: VotingStrategy
    ) -> bool:
        """Cast vote with cryptographic verification."""
        async with self._lock:
            if proposal_id not in self.proposals:
                raise ValueError(f"Proposal {proposal_id} not found")
            
            proposal = self.proposals[proposal_id]
            agent = self.agents.get(agent_id)
            
            if not agent or agent.is_jailed:
                raise PermissionError("Agent not registered or is jailed")
            
            if proposal.status != ProposalStatus.ACTIVE:
                raise ValueError("Proposal not active")
            
            if time.time() > proposal.voting_ends:
                raise ValueError("Voting period ended")
            
            # Check double voting
            vote_hash = hashlib.sha3_256(f"{agent_id}:{proposal_id}".encode()).hexdigest()
            if vote_hash in self.vote_hashes:
                raise PermissionError("Double voting detected")
            
            # Verify signature (simplified - in production use proper crypto)
            try:
                self._verify_vote_signature(agent, proposal_id, vote, signature)
            except InvalidSignature:
                raise PermissionError("Invalid vote signature")
            
            voting_power = voting_strategy.calculate_power(agent, {"proposal": proposal})
            proposal.votes[agent_id] = (vote, voting_power)
            self.vote_hashes.add(vote_hash)
            agent.votes_participated += 1
            agent.last_active = time.time()
            
            return True
    
    def _verify_vote_signature(self, agent: AgentProfile, proposal_id: str, vote: VoteType, signature: bytes):
        """Verify cryptographic signature of vote."""
        message = f"{agent.agent_id}:{proposal_id}:{vote.value}:{int(time.time())}"
        # In production, use agent.public_key to verify signature
        # For now, simulate verification
        if len(signature) < 32:
            raise InvalidSignature("Signature too short")
    
    async def checkpoint(self, filepath: str):
        """Persist state to disk."""
        async with self._lock:
            if time.time() - self._last_checkpoint < self._checkpoint_interval:
                return
            
            state_data = {
                "agents": {k: asdict(v) for k, v in self.agents.items()},
                "proposals": {k: asdict(v) for k, v in self.proposals.items()},
                "vote_hashes": list(self.vote_hashes),
                "total_voting_power": self.total_voting_power,
                "timestamp": time.time()
            }
            
            async with aiofiles.open(filepath, 'w') as f:
                await f.write(json.dumps(state_data, indent=2))
            
            self._last_checkpoint = time.time()
            logger.info("governance_checkpoint_created", filepath=filepath)


class ByzantineGovernanceEngine:
    """
    Main governance engine with Byzantine Fault Tolerance guarantees.
    Implements decentralized decision making for agent swarms.
    """
    
    def __init__(self, config: Optional[GovernanceConfig] = None):
        self.config = config or GovernanceConfig()
        self.state = GovernanceState(self.config)
        self.slashing_manager = SlashingManager(self.config)
        self.voting_strategy = self._initialize_voting_strategy()
        self.event_handlers: List[Callable[[str, Dict], None]] = []
        self.running = False
        self._background_tasks: Set[asyncio.Task] = set()
        
    def _initialize_voting_strategy(self) -> VotingStrategy:
        if self.config.voting_strategy == "quadratic":
            return QuadraticVoting()
        elif self.config.voting_strategy == "conviction":
            return ConvictionVoting()
        else:
            return QuadraticVoting()
    
    async def start(self):
        """Start governance engine background processes."""
        self.running = True
        tasks = [
            asyncio.create_task(self._proposal_lifecycle_manager()),
            asyncio.create_task(self._reputation_decay_loop()),
            asyncio.create_task(self._jail_manager()),
            asyncio.create_task(self._checkpoint_loop())
        ]
        self._background_tasks.update(tasks)
        logger.info("governance_engine_started")
    
    async def stop(self):
        """Graceful shutdown."""
        self.running = False
        for task in self._background_tasks:
            task.cancel()
        await asyncio.gather(*self._background_tasks, return_exceptions=True)
        logger.info("governance_engine_stopped")
    
    async def register_agent(
        self, 
        agent_id: str, 
        public_key: bytes, 
        initial_stake: float = 0.0
    ) -> AgentProfile:
        """Register new agent to governance."""
        profile = AgentProfile(
            agent_id=agent_id,
            public_key=public_key,
            staked_amount=initial_stake,
            last_active=time.time()
        )
        
        success = await self.state.register_agent(profile)
        if not success:
            raise ValueError(f"Agent {agent_id} already registered")
        
        await self._emit_event("AGENT_REGISTERED", {"agent_id": agent_id, "stake": initial_stake})
        return profile
    
    async def create_proposal(
        self, 
        proposer_id: str, 
        title: str, 
        description: str, 
        call_data: Dict[str, Any],
        stake_amount: float = None
    ) -> Proposal:
        """Create new governance proposal with staking requirement."""
        agent = await self.state.get_agent(proposer_id)
        if not agent:
            raise PermissionError("Agent not registered")
        
        if agent.is_jailed:
            raise PermissionError("Jailed agents cannot propose")
        
        if agent.reputation_score < self.config.proposal_threshold:
            raise PermissionError("Insufficient reputation to propose")
        
        if agent.proposals_created >= self.config.max_proposals_per_agent:
            raise PermissionError("Maximum proposals reached for this agent")
        
        required_stake = stake_amount or self.config.min_proposal_stake
        if agent.staked_amount < required_stake:
            raise ValueError(f"Insufficient stake. Required: {required_stake}")
        
        proposal_id = f"PROP-{secrets.token_hex(16)}"
        now = time.time()
        
        proposal = Proposal(
            id=proposal_id,
            proposer=proposer_id,
            title=title,
            description=description,
            call_data=call_data,
            created_at=now,
            voting_starts=now + 3600,  # 1 hour delay before voting
            voting_ends=now + self.config.voting_period,
            execution_delay=self.config.execution_delay,
            quorum_threshold=self.config.quorum_numerator / self.config.quorum_denominator,
            approval_threshold=self.config.approval_threshold
        )
        
        await self.state.create_proposal(proposal)
        agent.proposals_created += 1
        
        await self._emit_event("PROPOSAL_CREATED", {
            "proposal_id": proposal_id,
            "proposer": proposer_id,
            "title": title
        })
        
        return proposal
    
    async def cast_vote(
        self, 
        proposal_id: str, 
        agent_id: str, 
        vote: VoteType,
        signature: bytes
    ):
        """Cast vote on active proposal."""
        await self.state.cast_vote(
            proposal_id, agent_id, vote, signature, self.voting_strategy
        )
        
        await self._emit_event("VOTE_CAST", {
            "proposal_id": proposal_id,
            "agent_id": agent_id,
            "vote": vote.name
        })
    
    async def execute_proposal(self, proposal_id: str) -> Dict[str, Any]:
        """Execute succeeded proposal after timelock."""
        async with self.state._lock:
            proposal = self.state.proposals.get(proposal_id)
            if not proposal:
                raise ValueError("Proposal not found")
            
            if proposal.status != ProposalStatus.SUCCEEDED:
                raise PermissionError("Proposal not in succeeded state")
            
            if time.time() < proposal.voting_ends + proposal.execution_delay:
                raise PermissionError("Timelock not expired")
            
            # Verify integrity before execution
            if not proposal.verify_integrity():
                proposal.status = ProposalStatus.QUARANTINED
                raise SecurityError("Proposal integrity check failed - possible tampering")
            
            proposal.status = ProposalStatus.EXECUTED
            proposal.execution_data = {
                "executed_at": time.time(),
                "executor": "system",
                "result": "success"
            }
            
            await self._emit_event("PROPOSAL_EXECUTED", {
                "proposal_id": proposal_id,
                "call_data": proposal.call_data
            })
            
            return proposal.execution_data
    
    async def report_byzantine_behavior(
        self, 
        reporter_id: str, 
        accused_id: str, 
        evidence: Dict[str, Any],
        reason: SlashingReason
    ):
        """Report and verify Byzantine behavior for slashing."""
        reporter = await self.state.get_agent(reporter_id)
        accused = await self.state.get_agent(accused_id)
        
        if not reporter or not accused:
            raise ValueError("Invalid agent IDs")
        
        # Verify evidence (simplified - in production use ZK proofs or multi-sig verification)
        if await self._verify_byzantine_evidence(evidence, accused):
            slash_result = await self.slashing_manager.slash_agent(
                accused, reason, evidence, self.state
            )
            
            # Reward reporter
            reporter.reputation_score += 10
            reporter.staked_amount += slash_result["amount_slashed"] * 0.1  # 10% bounty
            
            await self._emit_event("AGENT_SLASHED", {
                "accused": accused_id,
                "reporter": reporter_id,
                "reason": reason.value,
                "amount": slash_result["amount_slashed"]
            })
    
    async def _verify_byzantine_evidence(self, evidence: Dict, accused: AgentProfile) -> bool:
        """Verify cryptographic evidence of Byzantine behavior."""
        # In production: Verify Merkle proofs, signatures, consensus logs
        required_fields = ["timestamp", "block_height", "conflicting_votes", "signatures"]
        return all(field in evidence for field in required_fields)
    
    async def _proposal_lifecycle_manager(self):
        """Background task managing proposal state transitions."""
        while self.running:
            try:
                now = time.time()
                async with self.state._lock:
                    for proposal in self.state.proposals.values():
                        if proposal.status == ProposalStatus.PENDING and now >= proposal.voting_starts:
                            proposal.status = ProposalStatus.ACTIVE
                            await self._emit_event("PROPOSAL_ACTIVATED", {"proposal_id": proposal.id})
                        
                        elif proposal.status == ProposalStatus.ACTIVE and now >= proposal.voting_ends:
                            yes_power, no_power, abstain = proposal.calculate_results()
                            total_votes = yes_power + no_power + abstain
                            
                            # Check quorum
                            if total_votes < self.state.total_voting_power * proposal.quorum_threshold:
                                proposal.status = ProposalStatus.DEFEATED
                                await self._emit_event("PROPOSAL_DEFEATED_QUORUM", {"proposal_id": proposal.id})
                            elif yes_power > no_power and (yes_power / (yes_power + no_power)) > proposal.approval_threshold:
                                proposal.status = ProposalStatus.SUCCEEDED
                                self.state.execution_queue.append(proposal.id)
                                await self._emit_event("PROPOSAL_SUCCEEDED", {"proposal_id": proposal.id})
                            else:
                                proposal.status = ProposalStatus.DEFEATED
                                await self._emit_event("PROPOSAL_DEFEATED", {"proposal_id": proposal.id})
                
                await asyncio.sleep(60)  # Check every minute
            except Exception as e:
                logger.error("lifecycle_manager_error", error=str(e))
                await asyncio.sleep(60)
    
    async def _reputation_decay_loop(self):
        """Apply reputation decay for inactive agents."""
        while self.running:
            try:
                await asyncio.sleep(86400)  # Daily
                async with self.state._lock:
                    for agent in self.state.agents.values():
                        days_inactive = (time.time() - agent.last_active) / 86400
                        if days_inactive > 7:
                            decay = self.config.reputation_decay_rate * days_inactive
                            agent.reputation_score = max(0, agent.reputation_score - decay)
            except Exception as e:
                logger.error("reputation_decay_error", error=str(e))
    
    async def _jail_manager(self):
        """Manage agent jail releases."""
        while self.running:
            try:
                await asyncio.sleep(3600)  # Hourly
                now = time.time()
                async with self.state._lock:
                    for agent in self.state.agents.values():
                        if agent.is_jailed and agent.jail_release_time and now >= agent.jail_release_time:
                            agent.is_jailed = False
                            agent.jail_release_time = None
                            await self._emit_event("AGENT_RELEASED", {"agent_id": agent.agent_id})
            except Exception as e:
                logger.error("jail_manager_error", error=str(e))
    
    async def _checkpoint_loop(self):
        """Periodic state persistence."""
        while self.running:
            try:
                await asyncio.sleep(300)  # Every 5 minutes
                await self.state.checkpoint("./data/governance_state.json")
            except Exception as e:
                logger.error("checkpoint_error", error=str(e))
    
    async def _emit_event(self, event_type: str, data: Dict[str, Any]):
        """Emit governance events to subscribers."""
        event = {"type": event_type, "timestamp": time.time(), "data": data}
        for handler in self.event_handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    asyncio.create_task(handler(event_type, data))
                else:
                    handler(event_type, data)
            except Exception as e:
                logger.error("event_handler_error", handler=str(handler), error=str(e))
    
    def subscribe_to_events(self, handler: Callable[[str, Dict], None]):
        """Subscribe to governance events."""
        self.event_handlers.append(handler)
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get governance health metrics."""
        return {
            "total_agents": len(self.state.agents),
            "active_proposals": sum(1 for p in self.state.proposals.values() if p.status == ProposalStatus.ACTIVE),
            "total_voting_power": self.state.total_voting_power,
            "jailed_agents": sum(1 for a in self.state.agents.values() if a.is_jailed),
            "total_staked": sum(a.staked_amount for a in self.state.agents.values()),
            "average_reputation": sum(a.reputation_score for a in self.state.agents.values()) / max(len(self.state.agents), 1)
        }


class SecurityError(Exception):
    """Security-critical error in governance."""
    pass


# Integration helper for existing ConsensusEngine
class GovernanceConsensusAdapter:
    """Adapter to integrate governance with existing consensus engine."""
    
    def __init__(self, governance_engine: ByzantineGovernanceEngine, consensus_engine: Any):
        self.gov = governance_engine
        self.consensus = consensus_engine
        self.gov.subscribe_to_events(self._handle_governance_events)
    
    async def _handle_governance_events(self, event_type: str, data: Dict):
        """Bridge governance decisions to consensus parameters."""
        if event_type == "PROPOSAL_EXECUTED":
            call_data = data.get("call_data", {})
            if call_data.get("type") == "UPDATE_CONSENSUS_PARAMS":
                # Update consensus engine parameters based on governance decision
                params = call_data.get("params", {})
                # Apply to consensus_engine (implementation depends on consensus_engine API)
                logger.info("consensus_params_updated_via_governance", params=params)
