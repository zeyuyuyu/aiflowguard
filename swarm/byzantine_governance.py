import hashlib
import random

class ByzantineGovernance:
    def __init__(self, num_nodes, f):
        self.num_nodes = num_nodes
        self.f = f
        self.quorum_size = num_nodes - f

    def propose_block(self, transactions):
        # Generate a new block proposal
        block = {
            'transactions': transactions,
            'timestamp': time.time(),
            'proposer': random.randint(0, self.num_nodes - 1)
        }
        block_hash = self.hash_block(block)
        return block, block_hash

    def validate_block(self, block, block_hash):
        # Validate the proposed block
        if self.hash_block(block) != block_hash:
            return False

        # Check for quorum agreement
        votes = [0] * self.num_nodes
        for i in range(self.num_nodes):
            # Simulate voting by each node
            if random.random() < 0.8:
                votes[i] = 1

        if sum(votes) >= self.quorum_size:
            return True
        else:
            return False

    def hash_block(self, block):
        # Hash the block using SHA-256
        block_string = str(block).encode('utf-8')
        return hashlib.sha256(block_string).hexdigest()
