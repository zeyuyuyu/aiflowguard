import random
import time

class ByzantineConsensusProtocol:
    def __init__(self, node_count, byzantine_threshold):
        self.node_count = node_count
        self.byzantine_threshold = byzantine_threshold
        self.node_states = [0] * node_count
        self.round = 0

    def run_round(self):
        self.round += 1
        print(f'Starting round {self.round}')

        # Byzantine nodes send random values
        byzantine_nodes = random.sample(range(self.node_count), self.byzantine_threshold)
        for node in byzantine_nodes:
            self.node_states[node] = random.randint(0, 1)
            print(f'Byzantine node {node} sent value {self.node_states[node]}')

        # Non-Byzantine nodes send their current state
        for node in range(self.node_count):
            if node not in byzantine_nodes:
                print(f'Node {node} sent value {self.node_states[node]}')

        # Nodes collect values and apply majority rule
        value_counts = [0, 0]
        for node_state in self.node_states:
            value_counts[node_state] += 1
        new_state = 0 if value_counts[0] > value_counts[1] else 1
        print(f'New state: {new_state}')

        # Update node states
        for node in range(self.node_count):
            self.node_states[node] = new_state

# Example usage
protocol = ByzantineConsensusProtocol(node_count=10, byzantine_threshold=3)
for _ in range(10):
    protocol.run_round()
    time.sleep(1)