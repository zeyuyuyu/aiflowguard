import hashlib
import time
from typing import Dict, List, Optional, Set
from dataclasses import dataclass
from enum import Enum

class VoteType(Enum):
    APPROVE = 'approve'
    REJECT = 'reject'
    ABSTAIN = 'abstain'

@dataclass
class Proposal:
    id: str
    description: str
    proposer: str
    timestamp: float
    expiration: float
    min_quorum: float
    votes: Dict[str, VoteType]
    weights: Dict[str, float]

class ByzantineGovernance:
    def __init__(self, min_validators: int = 4, vote_threshold: float = 0.66):
        self.validators: Set[str] = set()
        self.min_validators = min_validators
        self.vote_threshold = vote_threshold
        self.proposals: Dict[str, Proposal] = {}
        self.validator_weights: Dict[str, float] = {}
        
    def register_validator(self, validator_id: str, weight: float = 1.0) -> bool:
        """Register a new validator with optional voting weight"""
        if validator_id not in self.validators:
            self.validators.add(validator_id)
            self.validator_weights[validator_id] = weight
            return True
        return False

    def create_proposal(self, description: str, proposer: str, 
                       duration_hours: int = 72, min_quorum: float = 0.5) -> Optional[str]:
        """Create a new governance proposal"""
        if len(self.validators) < self.min_validators:
            return None
            
        proposal_id = hashlib.sha256(
            f'{description}{proposer}{time.time()}'.encode()
        ).hexdigest()[:16]
        
        self.proposals[proposal_id] = Proposal(
            id=proposal_id,
            description=description,
            proposer=proposer,
            timestamp=time.time(),
            expiration=time.time() + (duration_hours * 3600),
            min_quorum=min_quorum,
            votes={},
            weights=dict(self.validator_weights)
        )
        return proposal_id

    def cast_vote(self, proposal_id: str, validator: str, vote: VoteType) -> bool:
        """Cast a weighted vote on a proposal"""
        if proposal_id not in self.proposals:
            return False
            
        proposal = self.proposals[proposal_id]
        
        if validator not in self.validators:
            return False
            
        if time.time() > proposal.expiration:
            return False
            
        proposal.votes[validator] = vote
        return True

    def get_proposal_result(self, proposal_id: str) -> Optional[Dict]:
        """Calculate the weighted result of a proposal"""
        if proposal_id not in self.proposals:
            return None
            
        proposal = self.proposals[proposal_id]
        
        if time.time() < proposal.expiration:
            return None
            
        total_weight = sum(proposal.weights.values())
        vote_weights = {
            VoteType.APPROVE: 0,
            VoteType.REJECT: 0,
            VoteType.ABSTAIN: 0
        }
        
        for validator, vote in proposal.votes.items():
            vote_weights[vote] += proposal.weights[validator]
            
        participation = (vote_weights[VoteType.APPROVE] + 
                        vote_weights[VoteType.REJECT]) / total_weight
                        
        if participation < proposal.min_quorum:
            return {
                'status': 'failed',
                'reason': 'insufficient_quorum',
                'participation': participation
            }
            
        approval_ratio = vote_weights[VoteType.APPROVE] / (
            vote_weights[VoteType.APPROVE] + vote_weights[VoteType.REJECT]
        )
        
        return {
            'status': 'passed' if approval_ratio >= self.vote_threshold else 'rejected',
            'approval_ratio': approval_ratio,
            'participation': participation,
            'vote_weights': vote_weights
        }

    def get_active_proposals(self) -> List[str]:
        """Get list of active proposal IDs"""
        current_time = time.time()
        return [
            p_id for p_id, p in self.proposals.items() 
            if current_time <= p.expiration
        ]

    def get_proposal_status(self, proposal_id: str) -> Optional[Dict]:
        """Get current status of a proposal"""
        if proposal_id not in self.proposals:
            return None
            
        proposal = self.proposals[proposal_id]
        current_time = time.time()
        
        status = {
            'id': proposal_id,
            'description': proposal.description,
            'proposer': proposal.proposer,
            'active': current_time <= proposal.expiration,
            'time_remaining': max(0, proposal.expiration - current_time),
            'votes': len(proposal.votes),
            'participation': sum(
                proposal.weights[v] for v in proposal.votes.keys()
            ) / sum(proposal.weights.values())
        }
        
        if not status['active']:
            status.update(self.get_proposal_result(proposal_id) or {})
            
        return status